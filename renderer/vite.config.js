import { defineConfig } from "vite";
import basicSsl from "@vitejs/plugin-basic-ssl";

// Matches the spec's own directory layout (assets/ alongside src/, not
// nested under a Vite-conventional public/) -- publicDir: "assets" makes
// everything in assets/ served from the site root, same as public/ would.
export default defineConfig({
  publicDir: "assets",
  // basicSsl() generates a throwaway self-signed cert automatically.
  // Needed for real, not just nice-to-have: getUserMedia/getDisplayMedia
  // (camera/desktop vision, push-to-talk mic) are blocked by every browser
  // on a plain http:// origin unless that origin is exactly "localhost" --
  // confirmed live, a phone reaching this dev server over the LAN by IP
  // got "requires https or localhost" straight from the browser itself,
  // not from this app's own code. Every device that connects will see a
  // one-time "connection not private" warning to click through (a
  // self-signed cert is still a secure context as far as that API gate
  // cares, just not a trusted-cert one) -- mkcert is the alternative if
  // that warning becomes annoying, at the cost of installing its root CA
  // on every device that connects too.
  plugins: [basicSsl()],
  // host: true binds 0.0.0.0 instead of just localhost, so devices on
  // the LAN (e.g. a phone) can load the dev server too, not just this
  // machine -- matches Brain's own host: 0.0.0.0 in config.yaml.
  server: {
    host: true,
    proxy: {
      // Once this page is HTTPS (see basicSsl above), a browser refuses a
      // plain ws:// connection as mixed content unless the target is
      // literally "localhost" -- a phone reaching Brain by LAN IP would
      // hit that wall next. Routing through Vite's own dev-server proxy
      // instead means the Renderer only ever opens a wss:// connection to
      // this same already-trusted origin; Vite forwards it server-side
      // (Node-to-Python on this machine, not subject to browser policy)
      // to Brain's real plain-ws endpoint. Avoids standing up a second
      // TLS cert on Brain itself, which would need its own separate
      // manual trust step per device -- a WebSocket connection failure
      // can't be "clicked through" the way a page warning can, only a
      // page navigated to directly can prompt that. See main.js for the
      // client side of this (deriving the URL from location.host instead
      // of a hardcoded LAN IP in .env).
      //
      // xfwd passes each device's real address on to Brain (X-Forwarded-For),
      // so a device that keeps failing to log in only locks itself out -- without
      // it, every device looks like this PC to Brain and one stale tab locks out
      // all of them. See brain/main.py's _auth_ip.
      "/brain-ws": { target: "ws://127.0.0.1:8765", ws: true, changeOrigin: true, xfwd: true },
    },
  },
});
