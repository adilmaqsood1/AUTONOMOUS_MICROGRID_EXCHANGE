/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        background: {
          DEFAULT: "#F4F6F9",
          secondary: "#FFFFFF",
        },
        surface: {
          DEFAULT: "#FFFFFF",
          card: "#FFFFFF",
          elevated: "#FFFFFF",
          border: "#E2E8F0",
          hover: "#F8FAFC",
        },
        primary: {
          DEFAULT: "#2563EB", // Royal blue action
          hover: "#1D4ED8",
          glow: "rgba(37, 99, 235, 0.2)",
        },
        secondary: {
          DEFAULT: "#10B981", // Emerald green
          hover: "#059669",
          glow: "rgba(16, 185, 129, 0.2)",
        },
        accent: {
          blue: "#2563EB",
          cyan: "#06B6D4",
          purple: "#8B5CF6",
          amber: "#F59E0B",
          rose: "#EF4444",
        },
        sidebar: {
          DEFAULT: "#112330",
          border: "#1B3245",
          active: "#2563EB",
        },
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
}
