import { createBrowserRouter } from "react-router-dom";

import { CustomersPage } from "../CustomersPage";

export const adminRouter = createBrowserRouter([
  {
    path: "/customers",
    element: <CustomersPage />,
  },
]);
