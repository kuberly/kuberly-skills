/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Geist", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      colors: {
        // Dark slate palette tuned to the screenshot.
        bg: {
          DEFAULT: "#0b0d10",
          panel: "#11141a",
          card: "#161a21",
          hover: "#1c2129",
        },
        border: {
          DEFAULT: "rgba(255,255,255,0.07)",
          strong: "rgba(255,255,255,0.14)",
        },
        text: {
          DEFAULT: "#e6e8eb",
          muted: "#8a92a3",
          dim: "#5a626f",
        },
        accent: {
          blue: "#1677ff",
          orange: "#ff9900",
          red: "#ff5552",
          purple: "#a259ff",
          green: "#3ddc84",
          pink: "#ff4f9c",
          yellow: "#f5b800",
        },
        // Category colours used by both Dashboard arch grid and Graph nodes.
        cat: {
          edge: "#a259ff",
          compute: "#1677ff",
          data: "#3ddc84",
          network: "#ff9900",
          identity: "#f5b800",
          secrets: "#ff5552",
          registry: "#ff4f9c",
          obs: "#9da3ad",
          k8s: "#1677ff",
        },
      },
      boxShadow: {
        glow: "0 0 16px rgba(22,119,255,0.35)",
      },
    },
  },
  plugins: [],
};
