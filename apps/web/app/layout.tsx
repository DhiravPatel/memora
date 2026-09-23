import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Instrument_Serif, Schibsted_Grotesk } from "next/font/google";

import { Providers } from "./providers";
import "./globals.css";

/*
 * Three voices, each with one job.
 *
 * A high-contrast editorial serif for headlines and hero numerals gives the interface a
 * point of view that a grotesk alone cannot — it is the thing you remember. A quiet
 * grotesk carries the prose. A mono carries every label, id and figure, because this is a
 * data product and columns of numbers have to line up.
 */
const display = Instrument_Serif({
  subsets: ["latin"],
  weight: ["400"],
  style: ["normal", "italic"],
  variable: "--font-display",
  display: "swap",
});

const sans = Schibsted_Grotesk({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AI Memory Layer",
  description: "Persistent memory infrastructure for AI-powered SaaS.",
};

/* Matches --canvas, so browser chrome does not fight the paper ground. */
export const viewport: Viewport = {
  themeColor: "#faf9f7",
  colorScheme: "light",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${sans.variable} ${mono.variable}`}
      suppressHydrationWarning
    >
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
