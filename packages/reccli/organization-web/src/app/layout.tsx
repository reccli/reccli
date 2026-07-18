import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RecCli Org Console",
  description: "Observe and steer durable RecCli agent organizations.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
