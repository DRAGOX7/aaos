import type { Metadata } from "next";
import { Noto_Sans_Arabic, Plus_Jakarta_Sans } from "next/font/google";

import { AppShell } from "@/components/aaos/app-shell";
import { LocaleSync } from "@/components/aaos/locale-sync";
import { QueryProvider } from "@/components/providers/query-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LOCALE_INIT_SCRIPT } from "@/lib/i18n/locale";
import { THEME_INIT_SCRIPT } from "@/lib/theme";
import { cn } from "@/lib/utils";

import "./globals.css";

const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-jakarta",
});

// Plus Jakarta Sans has no Arabic letters; the browser takes those from here.
const notoArabic = Noto_Sans_Arabic({
  subsets: ["arabic"],
  variable: "--font-arabic",
});

export const metadata: Metadata = {
  title: "AAOS",
  description: "Academic AI Operating System",
  icons: {
    icon: "/icon.svg",
    shortcut: "/icon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // The inline scripts set the theme and language before first paint, so
    // <html> differs from the server HTML on purpose.
    <html
      lang="en"
      className={cn("font-sans", jakarta.variable, notoArabic.variable)}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        <script dangerouslySetInnerHTML={{ __html: LOCALE_INIT_SCRIPT }} />
      </head>
      <body>
        <QueryProvider>
          <TooltipProvider>
            <AppShell>{children}</AppShell>
          </TooltipProvider>
        </QueryProvider>
        <LocaleSync />
      </body>
    </html>
  );
}
