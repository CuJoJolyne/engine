"""
校准系数拟合工具 - 从手调参数反向拟合公式系数
"""
import numpy as np
from scipy.optimize import minimize
from lod_density_loader import SOGLODLoader


def fit_calibration_coefficients():
    """从已知手调参数拟合校准系数"""

    # 训练数据：手调参数
    training_data = [
        # (scene_path, manual_base, manual_distances, view_mode, target_count)
        (r'C:\Doc\AGP\LoD\nys_lod_0618', 50.0, [50, 100, 500, 1000], 'aerial', 1_000_000),
        (r'C:\Doc\AGP\LoD\syp_lod_0529', 10.0, [10, 30, 100, 300, 10000], 'ground', 1_000_000),
    ]

    # 提取特征
    features = []  # [N, L, target]
    labels = []    # [base]
    view_modes = []

    for scene_path, manual_base, manual_distances, view_mode, target in training_data:
        loader = SOGLODLoader(scene_path)
        stats = loader.get_statistics()
        N = stats['total_lod0_splats']
        L = loader.get_lod_levels()

        features.append([N, L, target])
        labels.append(manual_base)
        view_modes.append(view_mode)

        print(f"{view_mode:8s}: N={N:,}, L={L}, target={target:,} → base={manual_base}m")
        print(f"          manual distances={manual_distances}")

    features = np.array(features)
    labels = np.array(labels)

    print("\n" + "="*60)
    print("Fitting calibration coefficients...")
    print("="*60)

    # 模型：base = k × (target/N)^alpha × L^beta
    # 分aerial和ground两组系数

    def model(params, N, L, target, view_mode):
        """预测base"""
        if view_mode == 'aerial':
            k, alpha, beta = params[0:3]
        else:  # ground
            k, alpha, beta = params[3:6]

        ratio = target / N
        base = k * (ratio ** alpha) * (L ** beta)
        return base

    def loss(params):
        """MSE损失"""
        total_loss = 0.0
        for i, (N, L, target) in enumerate(features):
            pred = model(params, N, L, target, view_modes[i])
            true = labels[i]
            total_loss += (pred - true) ** 2
        return total_loss / len(labels)

    # 初始猜测：[k_aerial, alpha_aerial, beta_aerial, k_ground, alpha_ground, beta_ground]
    x0 = [180.0, 1/3, -0.3, 150.0, 1/3, -0.3]

    # 约束：k>0, 0<alpha<1, -1<beta<0
    bounds = [(10, 500), (0.1, 1.0), (-1.0, 0.0),
              (10, 500), (0.1, 1.0), (-1.0, 0.0)]

    result = minimize(loss, x0, bounds=bounds, method='L-BFGS-B')

    if result.success:
        fitted_params = result.x
        print(f"\nOptimization succeeded!")
        print(f"  Loss: {result.fun:.6f}")
        print(f"\nFitted coefficients:")
        print(f"  Aerial: k={fitted_params[0]:.2f}, alpha={fitted_params[1]:.4f}, beta={fitted_params[2]:.4f}")
        print(f"  Ground: k={fitted_params[3]:.2f}, alpha={fitted_params[4]:.4f}, beta={fitted_params[5]:.4f}")

        # 验证
        print(f"\n{'='*60}")
        print("Validation on training data:")
        print(f"{'='*60}")
        print(f"{'View':<8s} {'Manual':<10s} {'Predicted':<10s} {'Error':<10s}")
        print("-"*60)

        for i, (N, L, target) in enumerate(features):
            manual = labels[i]
            pred = model(fitted_params, N, L, target, view_modes[i])
            error = abs(pred - manual) / manual

            print(f"{view_modes[i]:<8s} {manual:<10.1f} {pred:<10.1f} {error*100:<10.1f}%")

        return fitted_params
    else:
        print(f"\nOptimization failed: {result.message}")
        return None


if __name__ == '__main__':
    coeffs = fit_calibration_coefficients()

    if coeffs is not None:
        print(f"\n{'='*60}")
        print("Recommended code:")
        print(f"{'='*60}")
        print(f"""
if view_mode == "aerial":
    k = {coeffs[0]:.2f}
    alpha = {coeffs[1]:.4f}
    beta = {coeffs[2]:.4f}
else:  # ground
    k = {coeffs[3]:.2f}
    alpha = {coeffs[4]:.4f}
    beta = {coeffs[5]:.4f}

base = k * (target / N) ** alpha * (L ** beta)
""")
