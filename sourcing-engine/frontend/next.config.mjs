/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Proxy API calls to the FastAPI backend so the browser talks same-origin
  // (no CORS needed in dev). Override the target with API_PROXY_TARGET.
  async rewrites() {
    const target = process.env.API_PROXY_TARGET || "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${target}/:path*` }];
  },
};

export default nextConfig;
