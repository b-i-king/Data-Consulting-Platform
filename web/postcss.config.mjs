// PostCSS runs Tailwind CSS during `next build` / `next dev`.
// Tailwind v4 needs only this one plugin — no tailwind.config.js file.
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
