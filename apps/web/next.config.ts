import type { NextConfig } from 'next';
const nextConfig: NextConfig = {
  distDir: process.env.DAYZERO_NEXT_DIST_DIR || '.next',
  async rewrites() { return [{ source: '/api/:path*', destination: (process.env.BACKEND_PROXY_URL || 'http://127.0.0.1:8000') + '/:path*' }]; }
};
export default nextConfig;
