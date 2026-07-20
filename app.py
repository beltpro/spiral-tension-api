import math
import os

import psycopg2

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

def get_db_connection():
    """
    Opens a PostgreSQL connection using DATABASE_URL.

    This function is ONLY called by the Max Allowable
    Tension calculator, so if the database is unavailable,
    the other calculators continue working.
    """

    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable not configured.")

    return psycopg2.connect(database_url)

BELT_DATA = [
    ("Omni Grid-Omni Pro-Posidrive", 0.75, 150),
    ("Omni Grid-Omni Pro-Posidrive", 1.00, 200),
    ("Omni Grid-Omni Pro-Posidrive", 1.20, 400),
    ("Omni Grid-Omni Pro-Posidrive", 1.50, 400),

    ("Omni Flex", 1.00, 300),
    ("Omni Flex", 1.20, 500),

    ("Advantage", 0.75, 150),
    ("Advantage", 1.20, 200),
    ("Advantage", 2.00, 300),

    ("Reduced radius Omni Grid", 1.00, 150),

    ("Small radius Omni Grid", 0.75, 150),
    ("Small radius Omni Grid", 1.00, 150),

    ("Small radius Heavy Duty Omni Grid", 1.50, 400),

    ("Super small radius", 1.00, 150),

    ("Small radius Omni Flex", 1.00, 300),

    ("Space saver", 1.00, 150),
]


def initialize_belt_database():
    """
    Creates and populates the allowable-tension table when required.

    This function is called only by the new database routes.
    It is never called by the existing engineering calculators.
    """

    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS belt_allowable_tension (
                    id SERIAL PRIMARY KEY,
                    belt_type VARCHAR(150) NOT NULL,
                    pitch NUMERIC(8, 3) NOT NULL,
                    allowable_tension INTEGER NOT NULL,
                    UNIQUE (belt_type, pitch)
                );
            """)

            cursor.executemany("""
                INSERT INTO belt_allowable_tension (
                    belt_type,
                    pitch,
                    allowable_tension
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (belt_type, pitch)
                DO UPDATE SET
                    allowable_tension = EXCLUDED.allowable_tension;
            """, BELT_DATA)

        connection.commit()

    except Exception:
        if connection is not None:
            connection.rollback()

        raise

    finally:
        if connection is not None:
            connection.close()

# Keep this restricted to your actual site domain.
CORS(app, resources={
    r"/calculate": {"origins": "https://www.beltpro.com.br"},
    r"/cycles": {"origins": "https://www.beltpro.com.br"},
    r"/air-pressure": {"origins": "https://www.beltpro.com.br"},
    r"/belt-types": {"origins": "https://www.beltpro.com.br"},
    r"/pitches": {"origins": "https://www.beltpro.com.br"},
    r"/max-tension": {"origins": "https://www.beltpro.com.br"},
})


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


class BeltCyclesCalculator:
    @staticmethod
    def calculate(straight_through, infeed_length, outfeed_length, tiers,
                  belt_width, tension_link, cage_radius, belt_speed,
                  hours_per_day, days_per_week):

        if tension_link <= 0:
            raise ValueError("Tension Link Location must be greater than 0.")
        if belt_speed <= 0:
            raise ValueError("Belt Speed must be greater than 0.")

        turn_ratio = cage_radius / tension_link

        active_belt_length = infeed_length + outfeed_length + (
            2 * math.pi * ((tension_link + cage_radius) / 12.0) * tiers
        )

        dwell_time = active_belt_length / belt_speed

        if dwell_time <= 0:
            raise ValueError("Calculated dwell time must be greater than 0.")

        if straight_through.strip().lower() == "n":
            cycles_per_hour = 2 * 60 / dwell_time
        else:
            cycles_per_hour = 60 / dwell_time

        cycles_per_day = cycles_per_hour * hours_per_day
        cycles_per_week = days_per_week * cycles_per_day
        cycles_per_year = cycles_per_week * 52

        if cycles_per_year <= 0:
            raise ValueError("Calculated fatigue cycles per year must be greater than 0.")

        years_to_100k = 100000 / cycles_per_year
        months_to_100k = int(years_to_100k * 12)

        return {
            "Turn Ratio": turn_ratio,
            "Active Belt Length": active_belt_length,
            "Dwell Time": dwell_time,
            "Fatigue Cycles Per Hour": cycles_per_hour,
            "Fatigue Cycles Per Day": cycles_per_day,
            "Fatigue Cycles Per Week": cycles_per_week,
            "Fatigue Cycles Per Year": cycles_per_year,
            "Years To 100000 Cycles": years_to_100k,
            "Months To 100000 Cycles": months_to_100k,
        }


CYCLES_REQUIRED_FIELDS = [
    "straight_through", "infeed_length", "outfeed_length", "tiers",
    "belt_width", "tension_link", "cage_radius", "belt_speed",
    "hours_per_day", "days_per_week",
]


class AirPressureCalculator:
    @staticmethod
    def calculate(belt_width, num_cylinders, cylinder_diameter, piston_diameter,
                  woven_belt, air_pressure_setting, span_sag, product_weight,
                  deflection_required, span_required):

        if cylinder_diameter <= piston_diameter:
            raise ValueError(
                "Air Cylinder Diameter must be greater than Piston Diameter."
            )

        area_per_cylinder = (
            math.pi * (cylinder_diameter / 2) ** 2
            - math.pi * (piston_diameter / 2) ** 2
        )
        total_area = area_per_cylinder * num_cylinders

        if total_area <= 0:
            raise ValueError("Total cylinder area must be greater than 0.")

        required_tension = belt_width * 100
        if woven_belt:
            required_tension *= 2

        calculated_air_pressure = required_tension / total_area

        belt_weight_per_ft = 4.2 * (belt_width / 12.0)

        if air_pressure_setting <= 0:
            raise ValueError("Air Pressure Setting must be greater than 0.")

        deflection = (
            (belt_weight_per_ft + product_weight) * span_sag ** 2
        ) / (96 * air_pressure_setting * total_area)

        if deflection_required <= 0:
            raise ValueError("Deflection Required must be greater than 0.")

        required_tension_drive = (
            (belt_weight_per_ft + product_weight) * span_required ** 2
        ) / (96 * deflection_required)

        required_air_pressure = required_tension_drive / total_area

        return {
            "Area Per Cylinder": area_per_cylinder,
            "Total Cylinder Area": total_area,
            "Total Required Take-Up Tension": required_tension,
            "Calculated Air Pressure Setting": calculated_air_pressure,
            "Belt Weight Per Linear Foot": belt_weight_per_ft,
            "Deflection Between Supports": deflection,
            "Required Tension At Drive": required_tension_drive,
            "Required Air Pressure": required_air_pressure,
        }


AIR_PRESSURE_REQUIRED_FIELDS = [
    "belt_width", "num_cylinders", "cylinder_diameter", "piston_diameter",
    "woven_belt", "air_pressure_setting", "span_sag", "product_weight",
    "deflection_required", "span_required",
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


@app.route("/cycles", methods=["POST"])
def cycles():
    data = request.get_json(silent=True) or {}

    missing = [f for f in CYCLES_REQUIRED_FIELDS if f not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        result = BeltCyclesCalculator.calculate(
            str(data["straight_through"]),
            float(data["infeed_length"]),
            float(data["outfeed_length"]),
            float(data["tiers"]),
            float(data["belt_width"]),
            float(data["tension_link"]),
            float(data["cage_radius"]),
            float(data["belt_speed"]),
            float(data["hours_per_day"]),
            float(data["days_per_week"]),
        )
    except (ValueError, TypeError) as ex:
        return jsonify({"error": f"Invalid input: {ex}"}), 400

    return jsonify({
        "turn_ratio": round(result["Turn Ratio"], 2),
        "active_belt_length": round(result["Active Belt Length"]),
        "dwell_time": round(result["Dwell Time"], 2),
        "cycles_per_hour": round(result["Fatigue Cycles Per Hour"], 2),
        "cycles_per_day": round(result["Fatigue Cycles Per Day"]),
        "cycles_per_week": round(result["Fatigue Cycles Per Week"]),
        "cycles_per_year": round(result["Fatigue Cycles Per Year"]),
        "years_to_100000_cycles": round(result["Years To 100000 Cycles"], 1),
        "months_to_100000_cycles": result["Months To 100000 Cycles"],
    })


@app.route("/air-pressure", methods=["POST"])
def air_pressure():
    data = request.get_json(silent=True) or {}

    missing = [f for f in AIR_PRESSURE_REQUIRED_FIELDS if f not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        result = AirPressureCalculator.calculate(
            float(data["belt_width"]),
            float(data["num_cylinders"]),
            float(data["cylinder_diameter"]),
            float(data["piston_diameter"]),
            bool(data["woven_belt"]),
            float(data["air_pressure_setting"]),
            float(data["span_sag"]),
            float(data["product_weight"]),
            float(data["deflection_required"]),
            float(data["span_required"]),
        )
    except (ValueError, TypeError) as ex:
        return jsonify({"error": f"Invalid input: {ex}"}), 400

    return jsonify({
        "area_per_cylinder": round(result["Area Per Cylinder"], 2),
        "total_cylinder_area": round(result["Total Cylinder Area"], 2),
        "total_required_tension": round(result["Total Required Take-Up Tension"]),
        "calculated_air_pressure": round(result["Calculated Air Pressure Setting"], 1),
        "belt_weight_per_ft": round(result["Belt Weight Per Linear Foot"], 2),
        "deflection": round(result["Deflection Between Supports"], 4),
        "required_tension_drive": round(result["Required Tension At Drive"]),
        "required_air_pressure": round(result["Required Air Pressure"], 1),
    })

@app.route("/belt-types", methods=["GET"])
def belt_types():

    try:
        initialize_belt_database()

        connection = get_db_connection()

        with connection.cursor() as cursor:

            cursor.execute("""
                SELECT DISTINCT belt_type
                FROM belt_allowable_tension
                ORDER BY belt_type;
            """)

            rows = cursor.fetchall()

        connection.close()

        return jsonify([row[0] for row in rows])

    except Exception as ex:

        return jsonify({
            "error": str(ex)
        }), 500

@app.route("/pitches", methods=["GET"])
def pitches():

    belt_type = request.args.get("belt_type", "").strip()

    if not belt_type:
        return jsonify({
            "error": "belt_type is required"
        }), 400

    try:
        initialize_belt_database()

        connection = get_db_connection()

        with connection.cursor() as cursor:

            cursor.execute("""
                SELECT pitch
                FROM belt_allowable_tension
                WHERE belt_type = %s
                ORDER BY pitch;
            """, (belt_type,))

            rows = cursor.fetchall()

        connection.close()

        if not rows:
            return jsonify({
                "error": "No pitches found for this belt type"
            }), 404

        return jsonify([float(row[0]) for row in rows])

    except Exception as ex:

        return jsonify({
            "error": str(ex)
        }), 500


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
