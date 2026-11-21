import { clerkMiddleware } from "@clerk/nextjs/server";

export default clerkMiddleware(
  async (auth, { nextUrl }) => {
    const { pathname } = nextUrl;
    if (pathname === "/dashboard" || pathname.startsWith("/dashboard/")) {
      const { isAuthenticated, redirectToSignIn } = await auth();
      if (!isAuthenticated) return redirectToSignIn();
    }
  },
  { signInUrl: "/sign-in", signUpUrl: "/sign-up" },
);

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/(.*)",
    "/dashboard/:path*",
  ],
};
