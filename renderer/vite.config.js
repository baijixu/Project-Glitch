import { defineConfig } from "vite";

// Matches the spec's own directory layout (assets/ alongside src/, not
// nested under a Vite-conventional public/) -- publicDir: "assets" makes
// everything in assets/ served from the site root, same as public/ would.
export default defineConfig({
  publicDir: "assets",
});
