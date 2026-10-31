import { Navigate, Route, Routes } from "react-router-dom";
import Shell from "./components/Shell";
import ShipmentsPage from "./pages/ShipmentsPage";
import UploadPage from "./pages/UploadPage";
import ProcessingPage from "./pages/ProcessingPage";
import ReviewPage from "./pages/ReviewPage";
import VerifyPage from "./pages/VerifyPage";
import ExportPage from "./pages/ExportPage";

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<ShipmentsPage />} />
        <Route path="/shipments/:id/upload" element={<UploadPage />} />
        <Route path="/shipments/:id/processing" element={<ProcessingPage />} />
        <Route path="/shipments/:id/review" element={<ReviewPage />} />
        <Route path="/shipments/:id/verify" element={<VerifyPage />} />
        <Route path="/shipments/:id/export" element={<ExportPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
