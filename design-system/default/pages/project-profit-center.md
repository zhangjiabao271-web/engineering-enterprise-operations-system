# Project Profit Center Page Override

- Treat every project as an independent profit unit. Never combine `澄湖` and `蓝湾` merely because they belong to one future annual contract.
- Keep four primary KPI cards only: settled revenue, total cost, confirmed gross profit, and operating cash balance.
- Show contract value, recorded output, accepted output, settlement, invoice, and receipt as separate stages.
- Show procurement, uniquely attributed labor, and manually entered other costs as separate cost sources.
- Show the selected project's operating stage and outstanding data gaps in this page, without adding another KPI card. Keep the wording consistent with the operating dashboard.
- From the selected project, offer keyboard-accessible links to contract/settlement and invoice/receipt pages; the target pages must open with that project selected. Direct sidebar navigation still opens their all-project views.
- Provide a visible, keyboard-accessible full-project cost drill-down from the cost section. Procurement drills to material lines plus freight; labor and other costs retain their source references. Each tab total must reconcile to the project summary in integer cents.
- Label shared-purchase material-line splits as display-only proportions; the allocated order share is authoritative. Cost attribution is not proof of cash payment.
- Never present recorded or accepted construction value as recognized revenue.
- Never present gross profit and cash balance as interchangeable metrics.
- Display missing project attribution as a visible text guardrail; color alone is insufficient.
- Use dense flat cards, neutral table rows, tabular currency figures, and no decorative charts without a defined target.
- Manual entries must explain when source-module values are already automatic so users do not double count them.
- Keep dialog actions fixed, allow resizing and mouse-wheel scrolling, and preserve visible keyboard focus.

## Local web desktop supplement

- Web accounting analysis is a tab under the project ledger, not a duplicate primary workspace. Old profit links remain compatible.
- Its four cards show recognized income, all costs, book profit and outstanding receivables. Confirmed profit and pending-project differences are explained separately; operating net cash stays in the project rows and is not labelled a real bank balance.
- Book profit always includes all recognized income and costs. Confirmed profit includes only accountable projects; pending-project book differences remain visible and reconcile with the book total. Both views use the same batch guardrail service and one read snapshot.
- Each amount opens the existing project-ledger detail or original cost source. Do not create another contract, receipt or expense editor.

- The cost dialog shows the whole project lifecycle, with Chinese headers and procurement, labor, and other-cost tabs. Show total cost and each source subtotal in a compact summary before the detail rows, without repeated boilerplate hints competing with the procurement facts.
- Procurement prioritizes material/specification, quantity with unit, the original unit price with tax basis, the full tax-inclusive material-line amount, and the current project's allocated cost. Supplier and date are secondary in the material cell; source number, payment status, tax breakdown, purpose and weigh ticket expand under “查看明细”. Do not use the whole order total as a material-line amount.
- Preserve exact weight-price precision and weight units; total-based purchases say “整批结算” instead of inventing a unit price. Freight is a separate row with the full freight and current project share, not a fabricated quantity or unit price. At narrow widths the five-column procurement table becomes stacked rows so all four purchasing figures remain visible without sideways scrolling.
- Reconcile category totals and summary in integer cents. Shared-purchase proportions are display only, never proof of payment.
- Keep the dialog footer reachable and wide detail tables internally scrollable. Dialog entrance/exit follows the shared cancelable motion controller and respects reduced motion.
