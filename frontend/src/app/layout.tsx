import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RecoveryAI — Indian B2B Payment Recovery",
  description:
    "Autonomous, bounded AI revenue recovery system for Indian B2B and consumer payment workflows. Powered by Gemini + Sarvam AI.",
  keywords: ["payment recovery", "India", "B2B", "AI", "UPI", "NACH", "invoice"],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body>{children}</body>
    </html>
  );
}
