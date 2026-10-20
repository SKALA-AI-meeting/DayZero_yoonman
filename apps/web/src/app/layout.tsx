import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Day Zero · 함께 일하며 발견하는 가능성', description: 'AI 동료와 함께하는 가상 업무 세션' };
export default function Layout({children}: {children: React.ReactNode}) {
  return <html lang="ko"><body>{children}</body></html>;
}
