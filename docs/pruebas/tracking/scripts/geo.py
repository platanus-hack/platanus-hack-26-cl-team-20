"""Modelo de la consola (calib.js): px de la foto -> quitar distorsión radial -> homografía -> mm. Espejo exacto."""
import numpy as np, cv2

class Calib:
    def __init__(self, matrix, image_px=(1280, 720), lens=None):
        self.H = np.array(matrix, float).reshape(3, 3); self.Hi = np.linalg.inv(self.H)
        self.image_px = image_px; self.lens = lens

    def undistort(self, p):
        if not self.lens: return np.asarray(p, float)
        L = self.lens; p = np.asarray(p, float)
        xd = (p[..., 0] - L["cx"]) / L["f"]; yd = (p[..., 1] - L["cy"]) / L["f"]; x, y = xd.copy(), yd.copy()
        for _ in range(12):
            r2 = x * x + y * y; k = 1 + L["k1"] * r2 + L["k2"] * r2 * r2; x, y = xd / k, yd / k
        return np.stack([x * L["f"] + L["cx"], y * L["f"] + L["cy"]], -1)

    def distort(self, p):
        if not self.lens: return np.asarray(p, float)
        L = self.lens; p = np.asarray(p, float)
        x = (p[..., 0] - L["cx"]) / L["f"]; y = (p[..., 1] - L["cy"]) / L["f"]; r2 = x * x + y * y
        k = 1 + L["k1"] * r2 + L["k2"] * r2 * r2
        return np.stack([x * k * L["f"] + L["cx"], y * k * L["f"] + L["cy"]], -1)

    @staticmethod
    def _apply(H, p):
        p = np.asarray(p, float); q = np.concatenate([p, np.ones(p.shape[:-1] + (1,))], -1) @ H.T
        return q[..., :2] / q[..., 2:3]

    def px_to_mm(self, p, scale=1.0):
        """p en px de una foto cuyo ancho es image_px[0]*scale."""
        return self._apply(self.H, self.undistort(np.asarray(p, float) / scale))

    def mm_to_px(self, p, scale=1.0):
        return self.distort(self._apply(self.Hi, p)) * scale

    def rectify(self, img, W=1000, Hh=563, ppmm=1.0):
        """Vista cenital en mm (ppmm px por mm) desde una foto de cualquier tamaño."""
        scale = img.shape[1] / self.image_px[0]
        xs, ys = np.meshgrid((np.arange(int(W * ppmm)) + 0.5) / ppmm, (np.arange(int(Hh * ppmm)) + 0.5) / ppmm)
        px = self.mm_to_px(np.stack([xs, ys], -1), scale).astype(np.float32)
        return cv2.remap(img, px[..., 0], px[..., 1], cv2.INTER_LINEAR)
