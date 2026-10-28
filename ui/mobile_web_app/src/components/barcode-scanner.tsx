import { useEffect, useRef, useState } from "react";
import { BrowserMultiFormatReader, type IScannerControls } from "@zxing/browser";
import { ScanLine, X, Keyboard, Camera } from "lucide-react";
import { Button } from "./field-ui";

type Props = {
  // Called with the decoded (or manually entered) barcode value.
  onResult: (value: string) => void;
  // Called when the user dismisses the scanner without a result.
  onClose: () => void;
};

/**
 * Full-screen barcode scanner backed by the device camera (via @zxing/browser).
 * Falls back to manual entry if the camera is unavailable, permission is
 * denied, or the user prefers to type the code.
 *
 * Note: camera access requires a secure context (HTTPS or localhost) and the
 * user granting permission.
 */
export function BarcodeScanner({ onResult, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const controlsRef = useRef<IScannerControls | null>(null);
  const [mode, setMode] = useState<"camera" | "manual">("camera");
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState("");

  useEffect(() => {
    if (mode !== "camera") return;
    let cancelled = false;
    const reader = new BrowserMultiFormatReader();

    async function start() {
      try {
        if (!navigator.mediaDevices?.getUserMedia) {
          throw new Error("Camera API not available in this browser.");
        }
        const controls = await reader.decodeFromVideoDevice(
          undefined, // let the browser pick (prefers rear camera on mobile)
          videoRef.current!,
          (result, err) => {
            if (result) {
              const text = result.getText();
              controls.stop();
              onResult(text);
            }
            // Per-frame decode errors (no barcode in view) are expected; ignore.
            void err;
          },
        );
        if (cancelled) {
          controls.stop();
          return;
        }
        controlsRef.current = controls;
      } catch (e) {
        if (cancelled) return;
        const name = e instanceof DOMException ? e.name : "";
        if (name === "NotAllowedError") {
          setError("Camera permission was denied. Enter the barcode manually below.");
        } else if (name === "NotFoundError" || name === "OverconstrainedError") {
          setError("No camera found. Enter the barcode manually below.");
        } else {
          setError(
            e instanceof Error
              ? `${e.message} You can enter the barcode manually below.`
              : "Could not start the camera. Enter the barcode manually below.",
          );
        }
        setMode("manual");
      }
    }

    void start();

    return () => {
      cancelled = true;
      controlsRef.current?.stop();
      controlsRef.current = null;
    };
  }, [mode, onResult]);

  return (
    <div className="modal-backdrop">
      <div className="modal scanner-modal">
        <Button variant="icon" className="close" aria-label="Close" onClick={onClose}>
          <X size={20} />
        </Button>

        {mode === "camera" ? (
          <>
            <h2>
              <ScanLine size={22} className="teal" /> Scan barcode
            </h2>
            <div className="scanner-viewport">
              <video ref={videoRef} className="scanner-video" muted playsInline />
              <div className="scanner-reticle" />
            </div>
            <p>Point the camera at the part barcode.</p>
            <Button variant="outline" onClick={() => setMode("manual")}>
              <Keyboard size={16} /> Enter manually
            </Button>
          </>
        ) : (
          <>
            <h2>
              <Keyboard size={22} className="teal" /> Enter barcode
            </h2>
            {error ? <p className="scanner-error">{error}</p> : null}
            <form
              className="scanner-manual"
              onSubmit={(e) => {
                e.preventDefault();
                const value = manual.trim();
                if (value) onResult(value);
              }}
            >
              <input
                autoFocus
                value={manual}
                onChange={(e) => setManual(e.target.value)}
                placeholder="e.g. PART-12345"
                aria-label="Barcode value"
              />
              <div className="modal-actions">
                <Button type="button" variant="outline" onClick={() => setMode("camera")}>
                  <Camera size={16} /> Use camera
                </Button>
                <Button type="submit" disabled={!manual.trim()}>
                  Look up
                </Button>
              </div>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
