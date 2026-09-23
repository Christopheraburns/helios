interface PlaceholderPageProps {
  title: string;
  description: string;
}

export default function PlaceholderPage({
  title,
  description,
}: PlaceholderPageProps) {
  return (
    <>
      <header className="page-header">
        <div>
          <p className="page-header__eyebrow">Helios workspace</p>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
      </header>
      <section className="placeholder-panel">
        <span className="placeholder-panel__mark" aria-hidden="true">
          H
        </span>
        <h2>{title} is ready for the next phase</h2>
        <p>
          This application shell reserves the workspace without introducing
          unfinished functionality.
        </p>
      </section>
    </>
  );
}
