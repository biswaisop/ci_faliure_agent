import { ClerkFailed, SignIn, SignUp } from "@clerk/nextjs";
import { Suspense } from "react";
import { AuthBrand, AuthBranding } from "./auth-branding";
import "./auth.css";

const termsUrl = process.env.NEXT_PUBLIC_TERMS_URL;
const privacyUrl = process.env.NEXT_PUBLIC_PRIVACY_URL;

const appearance = {
  variables: {
    colorPrimary: "#5455dc",
    colorPrimaryForeground: "#ffffff",
    colorSuccess: "#22c55e",
    colorDanger: "#f87171",
    colorNeutral: "#ffffff",
    colorForeground: "#fafafa",
    colorMutedForeground: "#a1a1aa",
    colorMuted: "#18181c",
    colorBackground: "#09090b",
    colorInput: "#111114",
    colorInputForeground: "#fafafa",
    colorBorder: "#27272a",
    colorRing: "#a5a7fc",
    fontSize: "0.875rem",
    borderRadius: "0.5rem",
  },
  options: {
    logoPlacement: "none",
    socialButtonsVariant: "blockButton",
    socialButtonsPlacement: "top",
  },
  captcha: { theme: "dark" },
  elements: {
    rootBox: { width: "100%" },
    cardBox: { width: "100%", boxShadow: "none", border: "0", overflow: "visible" },
    card: { padding: 0, boxShadow: "none", gap: "1.5rem" },
    header: { alignItems: "start", textAlign: "left", gap: "0.5rem" },
    headerTitle: { fontSize: "clamp(1.75rem, 4vw, 2.375rem)", lineHeight: 1.12, letterSpacing: "-0.052em", fontWeight: 650 },
    headerSubtitle: { lineHeight: 1.55 },
    socialButtonsBlockButton: { minHeight: "2.75rem", backgroundColor: "#16161a", borderColor: "#303036" },
    socialButtonsProviderIcon__github: { filter: "brightness(0) invert(1)" },
    formFieldInput: { minHeight: "2.75rem", fontSize: "1rem", borderColor: "#333339" },
    formButtonPrimary: { minHeight: "2.75rem", boxShadow: "none" },
    formFieldAction: { color: "#b0b2ff" },
    footer: { background: "none" },
    footerAction: { background: "none" },
    footerActionLink: { color: "#b0b2ff" },
  },
};

function AuthLoading() {
  return <p role="status" className="grid min-h-72 place-items-center text-sm text-[#a1a1aa]">Loading secure sign-in…</p>;
}

export function AuthLayout({ mode }) {
  const Widget = mode === "sign-in" ? SignIn : SignUp;

  return (
    <div className="auth-shell grid min-h-dvh bg-[#09090b] text-[#fafafa] lg:grid-cols-[55%_45%]">
      <AuthBranding />
      <main className="grid min-w-0 grid-rows-[auto_1fr_auto] px-5 pt-6 pb-5 sm:px-9 lg:pt-8">
        <header className="lg:hidden"><AuthBrand /></header>
        <div className="auth-enter col-start-1 row-start-2 mx-auto w-full max-w-[380px] self-center py-10">
          <Suspense fallback={<AuthLoading />}>
            <Widget appearance={appearance} fallback={<AuthLoading />} />
            <ClerkFailed>
              <p role="alert" className="mt-3 text-sm text-[#fca5a5]">
                Sign-in is temporarily unavailable. Reload this page to try again.
              </p>
            </ClerkFailed>
          </Suspense>
        </div>
        {(termsUrl || privacyUrl) && (
          <footer className="row-start-3 flex flex-wrap justify-center gap-x-5 gap-y-2 pb-2 text-xs text-[#a1a1aa] [&_a:hover]:text-[#fafafa] [&_a]:underline [&_a]:underline-offset-4">
            {termsUrl && <a href={termsUrl}>Terms of Service</a>}
            {privacyUrl && <a href={privacyUrl}>Privacy Policy</a>}
          </footer>
        )}
      </main>
    </div>
  );
}
