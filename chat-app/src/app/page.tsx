import { redirect } from "next/navigation";

/**
 * Root page — redirects to login.
 * The chat page has its own route protection to redirect unauthenticated
 * users back here; authenticated users who land on "/" will be redirected
 * to /login which then checks session state and routes accordingly.
 */
export default function RootPage() {
  redirect("/login");
}
