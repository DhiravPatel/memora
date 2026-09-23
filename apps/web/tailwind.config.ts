import type { Config } from "tailwindcss";

const config: Config = {
  // lib/ holds the badge colour maps — without it those classes are silently dropped.
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./hooks/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        canvas: "hsl(var(--canvas))",
        // Kept as an alias so `bg-background` in older markup still means the page.
        background: "hsl(var(--canvas))",
        surface: "hsl(var(--surface))",
        "surface-2": "hsl(var(--surface-2))",
        "surface-3": "hsl(var(--surface-3))",
        foreground: "hsl(var(--foreground))",
        "muted-foreground": "hsl(var(--muted-foreground))",
        faint: "hsl(var(--faint))",
        muted: "hsl(var(--surface-3))",
        border: "hsl(var(--border))",
        "border-strong": "hsl(var(--border-strong))",
        "border-ink": "hsl(var(--border-ink))",
        card: "hsl(var(--surface))",
        primary: "hsl(var(--accent))",
        "primary-foreground": "hsl(var(--accent-foreground))",
        accent: "hsl(var(--accent))",
        "accent-foreground": "hsl(var(--accent-foreground))",
        "accent-soft": "hsl(var(--accent-soft))",
        violet: "hsl(var(--violet))",
        danger: "hsl(var(--danger))",
        critical: "hsl(var(--critical))",
        success: "hsl(var(--success))",
        warning: "hsl(var(--warning))",
        info: "hsl(var(--info))",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "ui-serif", "Georgia", "serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      // Small and consistent. Enough to read as considered, not enough to go soft.
      borderRadius: {
        none: "0",
        sm: "4px",
        DEFAULT: "6px",
        md: "6px",
        lg: "10px",
        xl: "14px",
        "2xl": "18px",
        "3xl": "24px",
        full: "9999px",
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        DEFAULT: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        // The one hard edge left from the brutalist draft: primary buttons look pressable.
        press: "var(--shadow-press)",
        none: "none",
      },
      letterSpacing: {
        label: "0.11em",
        tightest: "-0.03em",
      },
      transitionTimingFunction: {
        swift: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
    },
  },
  plugins: [],
};

export default config;
