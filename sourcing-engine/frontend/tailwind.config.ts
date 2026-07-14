import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0a0b0f",
          900: "#0f1117",
          850: "#151823",
          800: "#1b1f2c",
          700: "#252a3a",
          600: "#333a4d",
        },
        accent: {
          DEFAULT: "#f26d5b",
          soft: "#f79284",
        },
        good: "#3fb98a",
        warn: "#e0b64a",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
      boxShadow: {
        panel: "0 1px 0 rgba(255,255,255,0.04) inset, 0 8px 30px rgba(0,0,0,0.35)",
      },
    },
  },
  plugins: [],
};

export default config;
