import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter, Route, Routes } from "react-router"

import "./index.css"
import { Shell } from "./routes/Shell"
import { Home } from "./routes/Home"
import { Player } from "./routes/Player"
import { Check } from "./routes/Check"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/" element={<Home />} />
          <Route path="/module/:slug" element={<Player />} />
          <Route path="/module/:slug/check" element={<Check />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
