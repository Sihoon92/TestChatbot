/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#F5F1EB",
        "paper-dark": "#EDE7DD",
        accent: "#D97757",
        "accent-dark": "#C2614A",
        ink: "#3D3A34",
      },
    },
  },
  plugins: [],
};
