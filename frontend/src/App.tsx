import { AuthProvider, useAuth } from "./auth/AuthContext";
import Footer from "./components/Footer";
import Home from "./pages/Home";
import Landing from "./pages/Landing";
import UpdatePassword from "./pages/UpdatePassword";

function Shell() {
  const { enabled, loading, session, recovering } = useAuth();
  if (!enabled && import.meta.env.PROD) {
    return <main><p className="warn" role="alert">This deployment is missing VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY.</p></main>;
  }
  if (loading) return <main><p className="muted">Loading…</p></main>;
  if (enabled && recovering) return <UpdatePassword />;
  if (enabled && !session) return <Landing />;
  return <Home />;
}

export default function App() {
  return (
    <div className="app">
      <header className="top">
        <h1 className="wordmark">FOOTBALL AI</h1>
        <p className="tagline">Three AI agents and 50,000 simulated matches for every fixture.</p>
      </header>
      <AuthProvider>
        <Shell />
      </AuthProvider>
      <Footer />
    </div>
  );
}
