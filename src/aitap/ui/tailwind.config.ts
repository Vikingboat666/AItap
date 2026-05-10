import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f5f7ff",
          100: "#e9eeff",
          200: "#c8d4ff",
          300: "#9fb1ff",
          400: "#6f86ff",
          500: "#475dff",
          600: "#2f43e6",
          700: "#2333b3",
          800: "#1c2989",
          900: "#172166",
        },
        ink: {
          50: "#f7f8fa",
          100: "#eef0f4",
          200: "#dde1e9",
          300: "#b9c1cf",
          400: "#8a93a5",
          500: "#5e6678",
          600: "#3f4757",
          700: "#2c3340",
          800: "#1d2330",
          900: "#101521",
        },
      },
      fontFamily: {
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};

export default config;
