# Accessibility

The goal: anyone can use the platform — with a keyboard only, a screen reader,
low vision, color blindness, or motion sensitivity — and as little typing as
possible, so fewer mistakes. The target standard is **WCAG 2.2 level AA**
(the usual legal and procurement bar in the US).

## What's in place

| Need | How the site handles it | Where |
|---|---|---|
| Keyboard only | Every control is a real button / link / input; **Skip to main content** is the first Tab stop; a gold **focus outline** shows where you are; dialogs (Esc closes, Cancel is focused first so Enter never confirms a delete); the Filter panel closes with Esc; pickers work with arrow keys + Enter | `app/layout.tsx`, `app/globals.css` (`:focus-visible`), `components/wizard/ui.tsx` |
| Screen readers | Page language `en-US`; one `<main>`; labeled header, nav and time bar; errors are announced (`role="alert"`) and invalid fields are marked (`aria-invalid`); charts have text labels and bar charts are real lists; tab bars hold only tabs; the two-step code box gets focus when it appears | `components/auth/AuthForm.tsx`, `components/charts/*` |
| Low vision | Text contrast at least **4.5:1** in both themes (checked with axe): a darker gold for text (`--color-brand-ink`), darker category colors, green and red that pass even on their own tinted pills; text scales with the browser's zoom | `app/globals.css` |
| Color blindness | Never color alone: changes have ▲ / ▼ and words ("On target"); links inside sentences are underlined; chart lines differ by dash too | KPI cards, `HBarChart`, `globals.css` |
| Motion sensitivity | "Reduce motion" on the computer turns off animations and transitions | `app/globals.css` |
| Downloaded dashboard | The same rules (passes axe in light and dark) | `data_skills/build-dashboard/templates/page.html` |

## Less typing, fewer mistakes

Every choice that can be a pick list is one: industry (NAICS lists), company
size, city (a searchable list, never free text), consultant, services,
dashboard filters, date ranges. Columns, types, dates and KPIs are detected
automatically; names are capitalized as you type; emails are checked before
sending; browsers can fill in names, emails, passwords and one-time codes
(`autocomplete`). When adding a new form, prefer a list or an automatic
default over a text box, and keep messages next to the field they're about.

## Keeping it that way

1. **Lint, on every pull request.** `npm run lint` includes every rule of
   `eslint-plugin-jsx-a11y`'s recommended set as **errors** (`web/eslint.config.mjs`):
   images without alt text, unlabeled fields, mouse-only controls and more fail
   the GitHub check.
2. **Scan pages after visual changes.** Install the free **axe DevTools**
   browser extension (or use Chrome / Edge → DevTools → Lighthouse →
   Accessibility). Scan in light AND dark mode. Target: 0 issues.
3. **Try it by keyboard.** Unplug the mouse: Tab through a page — can you see
   where you are, reach everything, and get out of every panel with Esc?
4. **Try a screen reader** now and then: Windows **Narrator** (Ctrl + Windows
   + Enter) or the free **NVDA**; on a Mac, **VoiceOver** (Cmd + F5).
5. **New colors:** check text colors at 4.5:1 against white, the page gray
   (`#f0f2f5`) and any tinted background they sit on.

Last full check (October 2026): axe (WCAG 2.2 A/AA + best practices) found 0
issues on home, sign-in, Get Started, Terms, Privacy, Forgot password, the
dashboard and Settings, in light and dark mode, and on the downloaded dashboard.
