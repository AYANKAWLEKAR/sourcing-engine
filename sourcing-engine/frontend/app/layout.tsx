import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Origo — Off-Market Sourcing",
  description: "Conversational off-market company sourcing with evidence-weighted ranking.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
