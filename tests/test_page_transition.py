import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from ui.page_transition import (
    PageTransition, _motion_allowed, _next_frame_delay, animate_dialog_open,
    motion_is_enabled,
)


class PageTransitionTests(unittest.TestCase):
    def setUp(self):
        preference = patch('ui.page_transition.motion_is_enabled', return_value=True)
        preference.start()
        self.addCleanup(preference.stop)

    def transition(self):
        transition = PageTransition.__new__(PageTransition)
        transition.host = Mock()
        transition.current = Mock()
        transition.previous = None
        transition._overlay = Mock()
        transition._image = object()
        transition._timer = None
        transition._started = 10
        transition.enabled = True
        transition._owner = SimpleNamespace()
        transition._retire_page = lambda page: page.destroy()
        return transition

    def test_frame_changes_only_overlay_opacity(self):
        transition = self.transition()
        with patch('ui.page_transition.monotonic', return_value=10.08):
            transition._step()
        transition.current.place_configure.assert_not_called()
        self.assertAlmostEqual(transition._overlay.attributes.call_args.args[1], 0.12375)
        delay, callback = transition.host.after.call_args.args
        self.assertTrue(1 <= delay <= 9)
        self.assertEqual(callback, transition._step)

    def test_finish_releases_cover_and_cancels_callback(self):
        transition = self.transition()
        overlay = transition._overlay
        transition._timer = 'timer'
        transition.previous = Mock()
        previous = transition.previous
        transition.finish()
        transition.host.after_cancel.assert_called_once_with('timer')
        previous.destroy.assert_called_once()
        overlay.destroy.assert_called_once()
        self.assertIsNone(transition._image)
        self.assertIsNone(transition._overlay)
        self.assertIsNone(transition._timer)

    def test_layout_must_be_quiet_before_animation_clock_starts(self):
        transition = self.transition()
        transition._ready_at = 10
        transition._last_probe = 10
        transition._quiet_frames = 0
        transition._begin = Mock()
        for moment in (10.2, 10.22):
            with patch('ui.page_transition.monotonic', return_value=moment):
                transition._wait_ready()
        transition._begin.assert_not_called()
        with patch('ui.page_transition.monotonic', return_value=10.24):
            transition._wait_ready()
        transition._begin.assert_called_once()

    def test_busy_layout_falls_back_without_animating(self):
        transition = self.transition()
        transition._ready_at = 10
        transition._last_probe = 11
        transition._quiet_frames = 0
        transition._begin = Mock()
        with patch('ui.page_transition.monotonic', return_value=12):
            transition._wait_ready()
        transition._begin.assert_not_called()
        self.assertIsNone(transition._overlay)

    def test_disabling_motion_cleans_pending_animation(self):
        transition = self.transition()
        transition._timer = 'timer'
        transition.set_enabled(False)
        self.assertFalse(transition.enabled)
        self.assertIsNone(transition._overlay)
        self.assertIsNone(transition._timer)

    def test_reused_frame_is_not_destroyed_on_retirement(self):
        transition = self.transition()
        transition.previous = Mock()
        previous = transition.previous
        transition._retire_page = lambda page: page.place_forget()
        transition.finish()
        previous.place_forget.assert_called_once()
        previous.destroy.assert_not_called()

    def test_interrupted_navigation_does_not_capture_or_queue_another_animation(self):
        transition = self.transition()
        previous = transition.current
        destination = Mock()
        transition._timer = 'old-timer'
        transition._cover_current = Mock()
        self.assertIs(transition.new_page(destination), destination)
        transition.host.after_cancel.assert_called_once_with('old-timer')
        transition._cover_current.assert_not_called()
        self.assertIs(transition.previous, previous)
        destination.lower.assert_called_once_with(previous)
        transition.host.after_idle.assert_called_once_with(transition._start)

    def test_same_frame_reentry_does_not_lower_itself(self):
        transition = self.transition()
        transition._cover_current = Mock()
        current = transition.current
        self.assertIs(transition.new_page(current), current)
        current.lower.assert_not_called()
        transition._cover_current.assert_not_called()
        transition.host.after_idle.assert_not_called()

    def test_reduced_motion_stops_an_inflight_animation(self):
        transition = self.transition()
        with patch('ui.page_transition.motion_is_enabled', return_value=False):
            transition._step()
        self.assertIsNone(transition._overlay)
        transition.host.after.assert_not_called()

    def test_completion_releases_snapshot(self):
        transition = self.transition()
        overlay = transition._overlay
        with patch('ui.page_transition.monotonic', return_value=10.2):
            transition._step()
        overlay.attributes.assert_called_once_with('-alpha', 0)
        self.assertIsNone(transition._image)
        transition.host.after.assert_not_called()

    def test_resize_and_owner_movement_finish_pending_motion(self):
        for method, widget in (('_on_resize', 'host'), ('_on_owner_change', '_owner')):
            with self.subTest(method=method):
                transition = self.transition()
                getattr(transition, method)(SimpleNamespace(widget=getattr(transition, widget)))
                self.assertIsNone(transition._overlay)

    def test_user_preference_never_overrides_system_reduced_motion(self):
        owner = SimpleNamespace(_motion_enabled=Mock())
        owner._motion_enabled.get.return_value = True
        with patch('ui.page_transition.motion_is_enabled', return_value=False):
            self.assertFalse(_motion_allowed(owner))
        owner._motion_enabled.get.return_value = False
        self.assertFalse(_motion_allowed(owner))

    def test_frame_deadlines_skip_late_frames(self):
        for moment in (10, 10.02, 10.113, 12):
            with self.subTest(moment=moment), patch('ui.page_transition.monotonic', return_value=moment):
                self.assertTrue(1 <= _next_frame_delay(10) <= 9)


class MotionPreferenceTests(unittest.TestCase):
    def test_environment_reduced_motion(self):
        with patch.dict('os.environ', ENGINEERING_REDUCED_MOTION='1'):
            self.assertFalse(motion_is_enabled())


class DialogMotionTests(unittest.TestCase):
    def test_long_form_construction_does_not_consume_the_fade(self):
        dialog = Mock()
        owner = SimpleNamespace()
        with patch('ui.page_transition.motion_is_enabled', return_value=True), patch(
            'ui.page_transition.monotonic', return_value=10
        ) as clock:
            animate_dialog_open(dialog, owner)
            clock.assert_not_called()
            step = dialog.after.call_args.args[1]
            clock.return_value = 20
            step()
            dialog.attributes.assert_called_with('-alpha', 0.84)
            clock.return_value = 20.08
            step()
            self.assertAlmostEqual(dialog.attributes.call_args.args[1], 0.98)
            clock.return_value = 20.17
            step()
            dialog.attributes.assert_called_with('-alpha', 1)

    def test_destroy_cancels_the_pending_dialog_callback(self):
        dialog = Mock()
        dialog.after.return_value = 'dialog-timer'
        with patch('ui.page_transition.motion_is_enabled', return_value=True):
            animate_dialog_open(dialog, SimpleNamespace())
        cancel = dialog.bind.call_args.args[1]
        cancel(SimpleNamespace(widget=dialog))
        dialog.after_cancel.assert_called_once_with('dialog-timer')

    def test_disabled_motion_leaves_the_dialog_opaque(self):
        dialog = Mock()
        with patch('ui.page_transition.motion_is_enabled', return_value=False):
            animate_dialog_open(dialog, SimpleNamespace())
        dialog.attributes.assert_not_called()
        dialog.after.assert_not_called()


if __name__ == '__main__':
    unittest.main()
