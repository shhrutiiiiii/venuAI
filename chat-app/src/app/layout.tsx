import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/contexts/AuthContext";
import { ThemeProvider } from "@/contexts/ThemeContext";
import ParticleBackground from "@/components/ParticleBackground";

export const metadata: Metadata = {
  title: "DRDO VenuAI — Defence Scientific R&D Knowledge Engine",
  description:
    "Official Defence Research and Development Organisation (DRDO) AI Knowledge Engine. Access lab directories, missile specs, radar platforms, and technical research documents.",
  keywords: ["DRDO", "Defence Research", "Ministry of Defence", "VenuAI", "RAG Engine", "Agni", "Uttam Radar", "Defence AI"],
  authors: [{ name: "DRDO VenuAI Intelligence Team" }],
  openGraph: {
    title: "DRDO VenuAI — Defence Scientific R&D Knowledge Engine",
    description:
      "Official Defence Research and Development Organisation (DRDO) AI Knowledge Engine.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning data-theme="dark">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="antialiased" suppressHydrationWarning>
        <ThemeProvider>
          {/* App-wide animated background — fixed behind all page content. */}
          <ParticleBackground />
          <div className="relative z-1">
            <AuthProvider>{children}</AuthProvider>
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}

