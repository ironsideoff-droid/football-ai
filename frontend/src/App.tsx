import Home from "./pages/Home";

export default function App() {
  return (
    <div className="app">
      <div className="ambient" aria-hidden><i /><i /><i /><span className="ballroll" /></div>
      <header className="top">
        <div className="topbar">
          <h1 className="wordmark"><img className="brand" src="/brand/logo-dark.png" alt="TACTIQ" /></h1>
        </div>
        <p className="tagline">Three AI agents and 50,000 simulated matches for every fixture.</p>
      </header>
      <Home />
    </div>
  );
}
