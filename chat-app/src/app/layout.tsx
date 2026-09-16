import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/contexts/AuthContext";
import { ThemeProvider } from "@/contexts/ThemeContext";

export const metadata: Metadata = {
  title: "VenuAI – AI-Powered Team Chat",
  description:
    "Real-time messaging, AI-assisted workflows, and seamless collaboration — all in one beautiful, fast workspace.",
  keywords: ["chat", "AI", "collaboration", "messaging", "team", "workspace"],
  authors: [{ name: "VenuAI" }],
  openGraph: {
    title: "VenuAI – AI-Powered Team Chat",
    description:
      "Real-time messaging, AI-assisted workflows, and seamless collaboration.",
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
      <body className="antialiased transition-colors duration-300" suppressHydrationWarning>
        <ThemeProvider>
          <AuthProvider>{children}</AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}

