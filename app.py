import math
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

CORS(app, resources={r"/calculate": {"origins": "https://www.beltpro.com.br"}})


class SpiralCalculator:
    @staticmethod
    def calculate(width, belt_weight, belt_pitch, product_weight,
                  bed_mu, edge_mu, inside_radius, cage_diameter,
                  tiers, tier_pitch, overdrive, initial_tension,
                  sprocket_teeth, direction):

        q = belt_weight + product_weight

        if overdrive <= bed_mu:
            raise ValueError(
                f"Overdrive ({overdrive}) must be greater than Bed Friction ({bed_mu}). "
                f"Values at or below Bed Friction make the Overdrive Correction formula "
                f"invalid and produce unrealistic tension results."
            )

        llti = (cage_diameter + 2 * width) * math.pi
        liti = math.sqrt(llti**2 + tier_pitch**2)

        p = (tier_pitch / 12.0) / (2 * math.pi * (2 * width + cage_diameter) / 24.0)

        if overdrive > 0:
            odc = (1 - bed_mu / overdrive) / math.sqrt(1 + (1 / overdrive) ** 2)
        else:
            odc = 1.0

        theta = tiers * 2 * math.pi

        A = odc * math.exp(edge_mu * theta)

        if abs(edge_mu) < 1e-9:
            B = theta
        else:
            B = (A - 1) / (odc * edge_mu)

        if direction == "Upward":
            k = bed_mu + p
        elif direction == "Downward":
            k = bed_mu - p
        else:
            k = bed_mu

        load_term = ((width + inside_radius) / 12.0) * q * k

        spiral_exit = (initial_tension + load_term * B) / A

        spiral_length = tiers * math.sqrt(
            (math.pi * (cage_diameter + 2 * width))**2 +
            tier_pitch**2
        ) / 12.0

        pitch_radius = sprocket_teeth * belt_pitch / (2 * math.pi)
        drive_torque = spiral_exit * pitch_radius

        return {
            "Spiral Exit Tension": spiral_exit,
            "Total Belt Length": spiral_length,
            "Drive Torque": drive_torque,
        }


REQUIRED_FIELDS = [
    "width", "belt_weight", "belt_pitch", "product_weight",
    "bed_mu", "edge_mu", "inside_radius", "cage_diameter",
    "tiers", "tier_pitch", "overdrive", "initial_tension",
    "sprocket_teeth", "direction",
]


@app.route("/calculate", methods=["POST"])
def calculate():
    data = request.get_json(silent=True) or {}

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        result = SpiralCalculator.calculate(
            float(data["width"]),
            float(data["belt_weight"]),
            float(data["belt_pitch"]),
            float(data["product_weight"]),
            float(data["bed_mu"]),
            float(data["edge_mu"]),
            float(data["inside_radius"]),
            float(data["cage_diameter"]),
            float(data["tiers"]),
            float(data["tier_pitch"]),
            float(data["overdrive"]),
            float(data["initial_tension"]),
            float(data["sprocket_teeth"]),
            str(data["direction"]),
        )
    except (ValueError, TypeError) as ex:
        return jsonify({"error": f"Invalid input: {ex}"}), 400

    return jsonify({
        "spiral_exit_tension": round(result["Spiral Exit Tension"]),
        "total_belt_length": round(result["Total Belt Length"]),
        "drive_torque": round(result["Drive Torque"]),
    })


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
