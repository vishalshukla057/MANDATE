---
name: PayPal Global Design System
colors:
  surface: '#f5faff'
  surface-dim: '#d4dbe0'
  surface-bright: '#f5faff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eef4fa'
  surface-container: '#e8eff4'
  surface-container-high: '#e2e9ef'
  surface-container-highest: '#dde3e9'
  on-surface: '#161c21'
  on-surface-variant: '#444652'
  inverse-surface: '#2a3136'
  inverse-on-surface: '#ebf2f7'
  outline: '#747683'
  outline-variant: '#c4c6d4'
  surface-tint: '#3959b0'
  primary: '#001d59'
  on-primary: '#ffffff'
  primary-container: '#003087'
  on-primary-container: '#7f9df8'
  inverse-primary: '#b4c5ff'
  secondary: '#005ab6'
  on-secondary: '#ffffff'
  secondary-container: '#0872e2'
  on-secondary-container: '#fefcff'
  tertiary: '#2e2000'
  on-tertiary: '#ffffff'
  tertiary-container: '#493400'
  on-tertiary-container: '#cd9800'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dbe1ff'
  primary-fixed-dim: '#b4c5ff'
  on-primary-fixed: '#00174b'
  on-primary-fixed-variant: '#1c4197'
  secondary-fixed: '#d7e3ff'
  secondary-fixed-dim: '#abc7ff'
  on-secondary-fixed: '#001b3f'
  on-secondary-fixed-variant: '#00458f'
  tertiary-fixed: '#ffdea1'
  tertiary-fixed-dim: '#f8bd32'
  on-tertiary-fixed: '#261900'
  on-tertiary-fixed-variant: '#5c4300'
  background: '#f5faff'
  on-background: '#161c21'
  surface-variant: '#dde3e9'
typography:
  display-hero:
    fontFamily: Inter
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
  display-hero-mobile:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  headline-sm:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  title-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: '400'
    lineHeight: 22px
  body-sm:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  label-code:
    fontFamily: Geist
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
  label-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
  label-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
rounded:
  sm: 0.5rem
  DEFAULT: 1rem
  md: 1.5rem
  lg: 2rem
  xl: 3rem
  full: 9999px
spacing:
  gutter: 1.5rem
  gutter-mobile: 1rem
  margin: 2rem
  margin-mobile: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.5rem
---

## Brand & Style

The design system embodies enterprise-grade trust, speed, and effortless global checkout clarity. Its visual tone balances modern fintech rigor with human accessibility. 

The aesthetic is Corporate / Modern with functional precision: pure whites, structured navy elevations, and vibrant navigational blues that reassure the user across consumer checkouts, merchant dashboards, and developer API consoles.

Key aesthetic characteristics:
- High contrast, friction-free readability across financial statements and code environments.
- Clean visual hierarchy with zero ornamental distortion.
- Trust cues delivered through deliberate color blocking, structured card surfaces, and instantly recognizable payment action points.

## Colors

The palette leverages iconic signature hues calibrated for AAA digital accessibility:

- **Primary (`#003087`)**: Core PayPal Navy. Anchors enterprise navigational chrome, headers, high-emphasis text, and core structural brand frames. Deep variant `#001C3E` serves as the root text color on light surfaces.
- **Secondary (`#0070E0`)**: Vibrant PayPal Blue. Drives active interactive elements, links, focus outlines, and primary system calls-to-action within developer surfaces.
- **Tertiary (`#FFC439`)**: Signature PayPal Gold. Strictly reserved for direct consumer checkout flows, express-payment buttons, and transactional hero conversion triggers. High-contrast dark text (`#001C3E`) is mandatory across gold fills.
- **Neutral (`#6C7378`)**: Balanced slate for secondary body copy, breadcrumbs, muted labels, and tabular metadata.
- **Surfaces**: Canvas begins at `#F5F7FA` (Neutral Cool Grey) with elevated containers set at `#FFFFFF`. Dividers and subtle enterprise hairline borders use `#E1E7EB`.
- **Validation**: Approvals, webhook success indicators, and positive transaction badges utilize PayPal Green (`#107C10`). Critical payment alerts and rollback states utilize Crimson (`#D9381E`).

## Typography

The type system prioritizes mathematical spacing, dense tabular stability, and high-speed glanceability.

- **Primary System Font (Inter)**: Handles brand headlines, checkout prompts, balance sheets, and narrative copy. Letter-spacing is set to `-0.015em` on headlines larger than 24px to ensure optical density.
- **Technical & Utility Font (Geist)**: Manages API endpoint paths, request payloads, API credentials, secret tokens, and webhook payloads in merchant/developer views.
- **Tabular Figures**: Numeric balance indicators, currency conversions, and ledger entries must explicitly enable `font-variant-numeric: tabular-nums` to maintain vertical alignment across continuous transactions.

## Layout & Spacing

The system runs on a 12-column fluid grid for desktop workspaces and switches to a 4-column responsive grid on mobile viewports.

- **Breakpoints**: Mobile (0–599px), Tablet (600–1023px), Desktop (1024–1439px), Wide Enterprise Console (1440px+).
- **Margins & Gutters**: Outer canvas margins use `2rem` on desktop and scale to `1rem` on mobile to maximize checkout form space. Column gutters default to `1.5rem` on desktop dashboards and `1rem` on mobile.
- **Component Rhythms**: Strict 8pt spatial grid for external margins; 4pt micro-adjustments reserved for badges, pill chips, and table cell heights. Checkout sheets are locked to a centered max-width container of `460px` to maintain focused visual paths.

## Elevation & Depth

Visual depth is achieved through clean surface containment, thin enterprise hairline borders, and ambient light physics rather than deep shadows.

- **Level 0 (Flat)**: Background `#F5F7FA` with `#FFFFFF` inner card surfaces bound by a 1px border of `#E1E7EB`. Used for dashboard tables, code containers, and settings screens.
- **Level 1 (Card / Container)**: `box-shadow: 0 1px 3px rgba(0, 28, 62, 0.06), 0 1px 2px rgba(0, 28, 62, 0.04)`. Applied to standard dashboard cards, summary modules, and balance snapshots.
- **Level 2 (Hover & Active Surfaces)**: `box-shadow: 0 4px 12px rgba(0, 48, 135, 0.08), 0 2px 4px rgba(0, 28, 62, 0.04)`. Applied to hovered transaction items, quick-filter dropdowns, and interactive pricing tables.
- **Level 3 (Modal / Checkout Sheet)**: `box-shadow: 0 12px 32px rgba(0, 28, 62, 0.12), 0 4px 8px rgba(0, 28, 62, 0.06)`. Applied to modal checkout dialogs, mobile bottom sheets, and critical MFA challenge surfaces.
- **Dark Mode Elevation**: Background surfaces drop to `#0B131F`, elevated cards take `#142032`, and borders take `#23354E` with an inner 1px border highlight rather than drop shadows.

## Shapes

The interface embraces a dual shape hierarchy:

- **Full Pill (`rounded-full` / `9999px`)**: Reserved for user-facing payment actions, consumer identity triggers, status pills, and the iconic PayPal checkout button. The pill geometry signifies human friendliness and quick, frictionless payment completion.
- **Structural Enterprise Form (`rounded-lg` / `0.5rem` to `1rem`)**: Applied to merchant dashboard cards, data tables, developer code blocks, and multi-field transaction inputs to ensure layout efficiency and clean vertical borders.

## Components

### Buttons
- **Consumer Primary ("The PayPal Button")**: Background `#FFC439`, text `#001C3E`, bold weight, fully rounded (`rounded-full`), min-height `48px`. Hover: `#F5B82E`. Focus: 2px offset ring with `#0070E0`.
- **Enterprise / Developer Primary**: Background `#0070E0`, text `#FFFFFF`, medium weight, rounded `0.5rem` (`rounded-md`), min-height `40px`. Hover: `#0058B3`.
- **Secondary**: Transparent background, 1.5px solid border `#0070E0`, text `#0070E0`. Hover: `#F0F7FD`.
- **Destructive**: Background `#FFFFFF`, border `#D9381E`, text `#D9381E`. Hover: `#FDEDED`.

### Cards & Payment Sheets
- Standalone cards feature a `#FFFFFF` fill, 1px solid `#E1E7EB` border, and `1rem` corner radius.
- Padding uses `space-lg` (`1.5rem`) on desktop and `space-md` (`1rem`) on mobile.
- Section headers within cards feature a 1px border-bottom divider using `#E1E7EB`.

### Input Fields & Selects
- Height: `44px` for enterprise inputs; `52px` for mobile checkout form fields.
- Border: 1px solid `#CAD4DC`, border-radius `0.5rem`.
- Focus State: Border color `#0070E0`, outer box-shadow `0 0 0 3px rgba(0, 112, 224, 0.2)`.
- Error State: Border color `#D9381E`, trailing warning glyph, and helper text in 12px `#D9381E`.

### Badges & Status Chips
- Height `24px`, fully rounded pill (`9999px`), padding `0 10px`, font size `12px`, weight `600`.
- **Success / Settled**: Background `#E6F4EA`, text `#107C10`.
- **Pending / In Review**: Background `#FEF7E0`, text `#946300`.
- **Failed / Chargeback**: Background `#FCE8E6`, text `#D9381E`.
- **Sandbox / Test**: Background `#E8F0FE`, text `#00457C`.

### Code Blocks & API Logs (Developer Console)
- Monospace font (`Geist`), background `#001C3E`, text `#E1E7EB`.
- Corner radius `0.5rem`, padding `1rem`, inline JSON syntax highlighting using `#FFC439` (keys) and `#38BDF8` (strings).
- Includes one-click "Copy Credentials" pill button fixed to top-right.

### Checkboxes & Radios
- Size `20px` x `20px`, border `1.5px solid #6C7378`.
- Checked state: Fill `#0070E0`, border `#0070E0`, inner white checkmark or center radio disc.