import { ClerkProvider } from "@clerk/nextjs";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import "./globals.css";

export const metadata = {
  title: "AutopCI · Diagnose failed GitHub Actions runs",
  description: "Turn failed pytest runs into a pull request diagnosis with cited evidence and a clear next step.",
};

const localization = {
  signIn: {
    start: {
      title: "Welcome back",
      subtitle: "Sign in to pick up your CI investigations.",
    },
  },
  signUp: {
    start: {
      title: "Create your account",
      subtitle: "Get to the root of your next failed build.",
    },
  },
};

export default function RootLayout({ children }) {
  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <ClerkProvider
          localization={localization}
          signInUrl="/sign-in"
          signUpUrl="/sign-up"
          signInFallbackRedirectUrl="/dashboard"
          signUpFallbackRedirectUrl="/dashboard"
          afterSignOutUrl="/sign-in"
        >
          {children}
        </ClerkProvider>
      </body>
    </html>
  );
}
