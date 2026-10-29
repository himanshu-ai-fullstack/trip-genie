import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trip Genie",
  description: "Find things to do anywhere you travel.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
