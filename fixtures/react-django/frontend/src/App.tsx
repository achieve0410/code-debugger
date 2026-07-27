import { Routes, Route, useNavigate } from "react-router-dom";
import { HomePage } from "./HomePage";
import { DetailsPage } from "./DetailsPage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/details" element={<DetailsPage />} />
    </Routes>
  );
}

export function NavigationButton() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/details")}>Details</button>;
}
