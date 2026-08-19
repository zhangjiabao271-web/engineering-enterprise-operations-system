# Project Management Page Override

- Use a master-detail layout: project list is the primary region; construction sites are the selected project's secondary region.
- Keep project status visible as text. Color may reinforce status but must not be the only signal.
- Primary CTA is `新增项目`; editing and closing are visually subordinate.
- Project creation/editing uses a centered modal with visible labels, grouped dates and inline validation.
- Preserve list context after save by reselecting the saved project.
- Closing a project and deactivating a site require confirmation and retain historical records.
- Tables use the global 34px row height and avoid adding optional columns that force horizontal overflow.
- At 1200px width, project and site tables remain vertically stacked.
- Keep the page chromatically restrained: `新增项目` uses the bronze-accent outline; no action uses a large filled color block.
- Editing, closing, site maintenance and deactivation use neutral outline actions.
- Do not render project statuses as high-contrast square chips or tint entire active rows.
- Express status with plain text; use muted text only for closed or inactive historical records.
- Form dialogs use the same light surface as the application, with no contrasting frame behind field labels.
