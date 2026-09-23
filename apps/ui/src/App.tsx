export default function App() {
  return (
    <div className="app">
      <header className="app__header">
        <a className="brand" href="/" aria-label="Helios home">
          <span className="brand__mark" aria-hidden="true">
            H
          </span>
          <span>Helios</span>
        </a>
      </header>

      <main className="app__main">
        <section className="welcome" aria-labelledby="welcome-title">
          <p className="welcome__eyebrow">Semantic intelligence for your data</p>
          <h1 id="welcome-title">Helios</h1>
          <p className="welcome__description">
            Your governed semantic workspace is ready.
          </p>
        </section>
      </main>
    </div>
  );
}
