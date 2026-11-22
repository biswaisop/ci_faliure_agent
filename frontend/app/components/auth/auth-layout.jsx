import { ClerkFailed, SignIn, SignUp } from "@clerk/nextjs";
import { Suspense } from "react";
import { Brand } from "@/components/brand";
import { AuthBranding } from "./auth-branding";
import "./auth.css";

const termsUrl = process.env.NEXT_PUBLIC_TERMS_URL;
const privacyUrl = process.env.NEXT_PUBLIC_PRIVACY_URL;

const appearance = {
  variables: {
    colorPrimary: "var(--color-accent)",
    colorPrimaryForeground: "var(--color-accent-ink)",
    colorSuccess: "var(--color-accent)",
    colorDanger: "#f87171",
    colorNeutral: "var(--color-foreground)",
    colorForeground: "var(--color-foreground)",
    colorMutedForeground: "var(--color-muted)",
    colorMuted: "var(--color-raised)",
    colorBackground: "var(--color-background)",
    colorInput: "var(--color-panel)",
    colorInputForeground: "var(--color-foreground)",
    colorBorder: "var(--color-line)",
    colorRing: "var(--color-accent)",
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
    socialButtonsBlockButton: { minHeight: "2.75rem", backgroundColor: "var(--color-panel)", borderColor: "var(--color-line)" },
    socialButtonsProviderIcon__github: { filter: "brightness(0) invert(1)" },
    formFieldInput: { minHeight: "2.75rem", fontSize: "1rem", borderColor: "var(--color-line)" },
    formButtonPrimary: { minHeight: "2.75rem", boxShadow: "none" },
    formFieldAction: { color: "var(--color-accent)" },
    footer: { background: "none" },
    footerAction: { background: "none" },
    footerActionLink: { color: "var(--color-accent)" },
  },
};

function AuthLoading() {
  return <p role="status" className="grid min-h-72 place-items-center text-sm text-muted">Loading secure sign-in…</p>;
}

export function AuthLayout({ mode }) {
  const Widget = mode === "sign-in" ? SignIn : SignUp;

  return (
    <div className="grid min-h-dvh lg:grid-cols-[55%_45%]">
      <AuthBranding />
      <main className="grid min-w-0 grid-rows-[auto_1fr_auto] px-5 pt-6 pb-5 sm:px-9 lg:pt-8">
        <header className="lg:hidden"><Brand /></header>
        <div className="auth-enter col-start-1 row-start-2 mx-auto w-full max-w-[380px] self-center py-10">
          <Suspense fallback={<AuthLoading />}>
            <Widget appearance={appearance} fallback={<AuthLoading />} />
            <ClerkFailed>
              <p role="alert" className="mt-3 text-sm text-failure">
                Sign-in is temporarily unavailable. Reload this page to try again.
              </p>
            </ClerkFailed>
          </Suspense>
        </div>
        {(termsUrl || privacyUrl) && (
          <footer className="row-start-3 flex flex-wrap justify-center gap-x-5 gap-y-2 pb-2 text-xs text-muted [&_a:hover]:text-accent [&_a]:underline [&_a]:underline-offset-4">
            {termsUrl && <a href={termsUrl}>Terms of Service</a>}
            {privacyUrl && <a href={privacyUrl}>Privacy Policy</a>}
          </footer>
        )}
      </main>
    </div>
  );
}
