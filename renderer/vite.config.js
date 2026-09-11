import { defineConfig } from "vite";

// Matches the spec's own directory layout (assets/ alongside src/, not
// nested under a Vite-conventional public/) -- publicDir: "assets" makes
// everything in assets/ served from the site root, same as public/ would.
export default defineConfig({
  publicDir: "assets",
  // host: true binds 0.0.0.0 instead of just localhost, so devices on
  // the LAN (e.g. a phone) can load the dev server too, not just this
  // machine -- matches Brain's own host: 0.0.0.0 in config.yaml.
  server: {
    host: true,
  },
});
