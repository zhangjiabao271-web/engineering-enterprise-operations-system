"""Ephemeral web conversations over the desktop's read-only assistant engine."""

from copy import deepcopy
import logging
import secrets
import threading
import time

from ai_client import AIError


class WebAssistant:
    def __init__(self, engine=None):
        self._engine = engine
        self._sessions = {}
        self._lock = threading.RLock()
        self._closed = False

    def _session(self, identity):
        if self._closed:
            raise ValueError('经营助手已关闭。')
        session = self._sessions.get(identity)
        if session and time.monotonic() - session['touched'] > 1800:
            self._cancel(session)
            del self._sessions[identity]
            session = None
        if not session:
            raise ValueError('对话已过期，请重新打开经营助手。')
        session['touched'] = time.monotonic()
        return session

    @staticmethod
    def _cancel(session):
        session['generation'] += 1
        if session['cancel']:
            session['cancel'].set()
        session.update(cancel=None, busy=False, stream='')

    def create(self):
        with self._lock:
            if self._closed:
                raise ValueError('经营助手已关闭。')
            for identity, session in list(self._sessions.items()):
                if time.monotonic() - session['touched'] > 1800:
                    self._cancel(session)
                    self._sessions.pop(identity)
            if len(self._sessions) >= 8:
                idle = [(key, value) for key, value in self._sessions.items() if not value['busy']]
                if not idle:
                    raise ValueError('同时查询的对话过多，请停止旧查询或稍后再试。')
                oldest, session = min(idle, key=lambda item: item[1]['touched'])
                self._cancel(session)
                del self._sessions[oldest]
            identity = secrets.token_urlsafe(24)
            self._sessions[identity] = dict(context={}, messages=[], project_id=None,
                generation=0, cancel=None, busy=False, stream='', dropped_messages=0, touched=time.monotonic())
            return {'session_id': identity, **self.snapshot(identity)}

    def snapshot(self, identity):
        with self._lock:
            session = self._session(identity)
            return deepcopy({key: session[key] for key in
                             ('messages', 'project_id', 'generation', 'busy', 'stream', 'dropped_messages')})

    def change(self, identity, *, project_id=None, clear=False):
        with self._lock:
            session = self._session(identity)
            self._cancel(session)
            session['context'] = {}
            if clear:
                session['messages'] = []
                session['dropped_messages'] = 0
            session['project_id'] = project_id
            if project_id:
                session['context']['project_id'] = project_id
            else:
                session['context'].pop('project_id', None)
            return self.snapshot(identity)

    def stop(self, identity):
        with self._lock:
            self._cancel(self._session(identity))
            return self.snapshot(identity)

    def entry_completed(self,identity,generation,token):
        from services.assistant_entry_service import get_draft
        draft=get_draft(token)
        if draft['state']!='applied':
            raise ValueError('草稿尚未保存，不记录完成状态')
        with self._lock:
            session=self._session(identity)
            if session['busy'] or generation!=session['generation']:
                raise ValueError('对话已变化，账目保存结果请从业务台账核对')
            message=session['messages'][-1]
            if message.get('message_type')!='entry_draft':
                raise ValueError('当前消息不是录入草稿')
            message.update(content=f"已保存并回读核对{len(draft['result'])}笔记录。成本、回款与资金流水分别记账，本次未额外生成资金流水。",
                           message_type='entry_saved',entry_records=None,entry_result=draft['result'])
            session['context'].pop('entry_records',None)
            return self.snapshot(identity)

    def confirm(self, identity, generation, index, *, allow_model=False):
        """Accept only a candidate produced by the latest server-side turn."""
        with self._lock:
            session = self._session(identity)
            if session['busy'] or type(index) is not int or type(generation) is not int:
                raise ValueError('请等待当前查询完成后，再选择候选。')
            message = session['messages'][-1] if session['messages'] else {}
            candidates = message.get('candidates') or []
            if generation != session['generation'] or message.get('generation') != generation or not 0 <= index < len(candidates):
                raise ValueError('候选已过期，请重新查询。')
            candidate = candidates[index]
            if type(allow_model) is not bool:
                raise ValueError('模型授权选项须明确勾选。')
            session['context'].update(deepcopy(candidate.get('context_updates') or {}))
            session['context']['pending_confirmation'] = None
            return self.ask(identity, message['question'], allow_model=allow_model,
                            _user_content='确认：' + str(candidate.get('label') or '该候选'))

    def ask(self, identity, question, *, allow_model=False, expected_generation=None, _user_content=None,mode='query'):
        question = str(question or '').strip()
        if not question or len(question) > 4000:
            raise ValueError('请输入1至4000字的经营问题。')
        if type(allow_model) is not bool:
            raise ValueError('模型授权选项须明确勾选。')
        if mode not in ('query','entry'):
            raise ValueError('请选择查账或协助录入模式')
        if mode=='entry' and not allow_model:
            raise ValueError('协助录入需要模型理解这段业务描述，请先明确授权；不会向模型发送原始账目')
        with self._lock:
            if self._closed:
                raise ValueError('经营助手已关闭。')
            session = self._session(identity)
            if expected_generation is not None and (type(expected_generation) is not int or expected_generation != session['generation']):
                raise ValueError('对话范围已变化，请核对当前范围后重新提问。')
            if session['busy']:
                raise ValueError('当前正在查询，请先停止或等待回答。')
            session['generation'] += 1
            generation = session['generation']
            cancel = threading.Event()
            history = deepcopy(session['messages'][-12:])
            context = deepcopy(session['context'])
            project_id = session['project_id']
            session['messages'].append({'role': 'user', 'content': _user_content or question,
                                        'generation': generation, 'project_id': project_id})
            session.update(busy=True, stream='', cancel=cancel)

        def chunk(value):
            with self._lock:
                if not cancel.is_set() and session['generation'] == generation:
                    session['stream'] = (session['stream'] + str(value))[-16000:]

        def work():
            try:
                if self._engine is None:
                    from ai_engine import ask_ai_turn
                    engine = ask_ai_turn
                else:
                    engine = self._engine
                if mode=='entry':
                    from services.assistant_entry_service import extract
                    extracted=extract(question,previous=context.get('entry_records'))
                    result={'answer':'已整理为录入草稿，尚未记账。请打开确认清单核对项目、日期、金额和缺失条件。',
                            'entry_records':extracted['records'],'entry_questions':extracted['questions'],
                            'sources':[],'message_type':'entry_draft',
                            'context_updates':{'entry_records':extracted['records']}}
                else:
                    result = engine(question, project_id=project_id, conversation_context=context,
                                    history=history, cancel_event=cancel, on_chunk=chunk, allow_model=allow_model)
            except AIError as error:
                if error.code == 'cancelled':
                    cancel.set()
                result = {'answer': str(error), 'message_type': 'error', 'error_code': error.code, 'sources': []}
            except Exception:
                logging.exception('Web assistant query failed')
                result = {'answer': '查询没有完成，请核对模型设置或稍后再试；不能据此认定没有业务记录。',
                          'message_type': 'error', 'sources': []}
            with self._lock:
                if self._closed or session['generation'] != generation:
                    return
                if cancel.is_set():
                    session.update(busy=False, cancel=None, stream='')
                    return
                for key, value in (result.get('context_updates') or {}).items():
                    if value is None:
                        session['context'].pop(key, None)
                    else:
                        session['context'][key] = deepcopy(value)
                if 'project_id' in (result.get('context_updates') or {}):
                    session['project_id'] = result['context_updates']['project_id']
                session['messages'].append({'role': 'assistant', 'content': result['answer'],
                    'question': question, 'generation': generation, 'project_id': session['project_id'],
                    **{key: deepcopy(result.get(key)) for key in
                       ('sources', 'candidates', 'message_type', 'answer_mode', 'error_code')}})
                session['messages'][-1].update({key:deepcopy(result.get(key)) for key in ('entry_records','entry_questions')})
                if mode=='query' and result.get('sources'):
                    from services.operating_attention_service import clock
                    session['messages'][-1]['evidence_note']={
                        'completed_at':clock().isoformat(timespec='seconds'),
                        'scope':'指定项目' if session['project_id'] else '全公司',
                        'limits':'金额由本地服务计算；已登记成本不代表成本已全部补齐，也不代表实际付款。依据变化后请重新查询。'}
                excess = max(0, len(session['messages']) - 40)
                session['dropped_messages'] += excess
                session['messages'] = session['messages'][excess:]
                session.update(busy=False, cancel=None, stream='')

        threading.Thread(target=work, daemon=True).start()
        return {'generation': generation}

    def close(self):
        with self._lock:
            self._closed = True
            for session in self._sessions.values():
                self._cancel(session)
