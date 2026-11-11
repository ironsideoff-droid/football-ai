import Home from "./pages/Home";

export default function App() {
  return (
    <div className="app">
      <header className="top">
        <h1 className="wordmark">FOOTBALL AI</h1>
        <p className="tagline">Three AI agents and 50,000 simulated matches for every fixture.</p>
      </header>
      <Home />
    </div>
  );
}
