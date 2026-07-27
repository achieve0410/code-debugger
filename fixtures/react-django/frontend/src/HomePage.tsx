import { SummaryCard } from "./SummaryCard";
import { NavigationButton } from "./App";
import { DynamicPanel } from "./dynamic";

export function HomePage() {
  const showBeta = true;

  async function loadItems() {
    await fetch("/api/items/?include=active");
  }

  const refreshItems = async (event: React.MouseEvent<HTMLButtonElement>): Promise<void> => {
    event.preventDefault();
    await fetch("/api/items/?refresh=true");
  };

  return (
    <main>
      <SummaryCard />
      {showBeta ? <BetaPanel /> : <FallbackPanel />}
      <button onClick={loadItems}>Load items</button>
      <button onClick={refreshItems}>Refresh items</button>
      <NavigationButton />
      {window.location.hash ? <DynamicPanel name={window.location.hash} /> : null}
    </main>
  );
}

function BetaPanel() {
  return <section>Beta branch</section>;
}

function FallbackPanel() {
  return <section>Fallback branch</section>;
}
