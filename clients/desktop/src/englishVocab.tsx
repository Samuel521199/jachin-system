import React from "react";
import ReactDOM from "react-dom/client";
import { EnglishVocabCoach } from "./components/EnglishVocab/EnglishVocabCoach";
import "./styles/globals.css";

ReactDOM.createRoot(document.getElementById("english-vocab-root")!).render(
  <React.StrictMode>
    <EnglishVocabCoach />
  </React.StrictMode>,
);
