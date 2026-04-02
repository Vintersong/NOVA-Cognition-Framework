# Design Brief: Snake Game — "Synthwave Arcade Revival"

## Aesthetic Direction

Retro-futuristic cleanliness. Evokes a classic arcade machine display reimagined with contemporary clarity. Sharp geometric forms, clear typography, and a limited high-contrast palette — no pixelation, no grunge, no neon-on-black cliché.

**The one thing someone will remember:** a magenta snake on a deep violet-blue field — instantly distinctive, zero ambiguity about what's the snake vs. what's the food.

## Font

**Audiowide** (Google Fonts)
- Geometric, slightly extended sans-serif
- Encapsulates retro-futuristic arcade without sacrificing legibility
- Loaded via `@import url('https://fonts.googleapis.com/css2?family=Audiowide&display=swap')`

## Color Palette (OKLCH)

| Token | Value | Use |
|---|---|---|
| `--color-background-dark` | `oklch(12% 0.02 280)` | Canvas + page background — deep violet-blue, not pure black |
| `--color-snake` | `oklch(70% 0.25 330)` | Snake body — vibrant magenta |
| `--color-food` | `oklch(85% 0.18 90)` | Food — bright energetic yellow, maximum contrast |
| `--color-text-light` | `oklch(95% 0.01 270)` | All text — off-white with cool tint |
| `--color-ui-accent` | `var(--color-snake)` | Buttons, borders — reuses magenta for cohesion |
| `--color-overlay-background` | `oklch(12% 0.02 280 / 0.8)` | Game-over overlay — semi-transparent dark |

## Motion

- **Easing:** `cubic-bezier(0.65, 0.05, 0.36, 1)` (ease-out-expo)
- **Game-over overlay:** fades in via `opacity` + `visibility` transition — elegant, not jarring
- **Button hover:** `translateY(-2px) scale(1.02)` — subtle lift, no bounce
- No elastic, no spring, no bounce easing anywhere

## What's Banned

- Glassmorphism
- Gradient text
- Pure black (`#000`) or pure white (`#fff`)
- Cyan-on-dark color scheme
- Inter, Roboto, Arial, Open Sans
