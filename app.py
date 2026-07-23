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
    ("Omni Grid-Omni Pro-Posidrive", 1.08, 200),
    ("Omni Grid-Omni Pro-Posidrive", 1.20, 400),
    ("Omni Grid-Omni Pro-Posidrive", 1.50, 400),

    ("Omni Flex", 1.08, 300),
    ("Omni Flex", 1.20, 500),

    ("Advantage", 0.75, 150),
    ("Advantage", 1.20, 200),
    ("Advantage", 2.00, 300),

    ("Reduced radius Omni Grid", 1.08, 150),

    ("Small radius Omni Grid", 0.75, 150),
    ("Small radius Omni Grid", 1.08, 150),

    ("Small radius Heavy Duty Omni Grid", 1.50, 400),

    ("Super small radius", 1.08, 150),

    ("Small radius Omni Flex", 1.08, 300),

    ("Space saver", 1.08, 150),
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
    r"/belt-weight-options": {"origins": "https://www.beltpro.com.br"},
    r"/belt-weight-packing": {"origins": "https://www.beltpro.com.br"},
    r"/stacker-drive-ratio": {"origins": "https://www.beltpro.com.br"},
    r"/belt-sprocket-conversion": {"origins": "https://www.beltpro.com.br"},
    r"/oven-band-force": {"origins": "https://www.beltpro.com.br"},
    r"/turn-ratio": {"origins": "https://www.beltpro.com.br"},
    r"/side-drive-sizing": {"origins": "https://www.beltpro.com.br"},
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


@app.route("/max-tension", methods=["GET"])
def max_tension():

    belt_type = request.args.get("belt_type", "").strip()
    pitch = request.args.get("pitch", "").strip()

    if not belt_type:
        return jsonify({
            "error": "belt_type is required"
        }), 400

    if not pitch:
        return jsonify({
            "error": "pitch is required"
        }), 400

    try:
        pitch_value = float(pitch)

        initialize_belt_database()

        connection = get_db_connection()

        with connection.cursor() as cursor:

            cursor.execute("""
                SELECT allowable_tension
                FROM belt_allowable_tension
                WHERE belt_type = %s
                  AND pitch = %s;
            """, (belt_type, pitch_value))

            row = cursor.fetchone()

        connection.close()

        if row is None:
            return jsonify({
                "error": "No allowable tension found"
            }), 404

        return jsonify({
            "belt_type": belt_type,
            "pitch": pitch_value,
            "maximum_allowable_tension": row[0],
            "unit": "lbs"
        })

    except ValueError:

        return jsonify({
            "error": "pitch must be numeric"
        }), 400

    except Exception as ex:

        return jsonify({
            "error": str(ex)
        }), 500



# ==========================================================
# BELT WEIGHT & PACKING CALCULATOR
# The engineering tables and formulas below run only on Render.
# ==========================================================

LBFT_TO_KGM = 1.4881639436
LB_TO_KG = 0.45359237
IN_TO_MM = 25.4
FT_TO_M = 0.3048

PACKING = {
    "pipe_diameter_in": 1.43,
    "belt_thickness_in": 0.50,
    "plywood_weight_lb_ft2": 2.85,
    "standard_roll_length_ft": 50,
    "maximum_box_length_in": 99,
    "packing_clearance_in": 6,
}

MESH_075 = {
    "none": ("No mesh", 0, 0),
    "12-16-17": ("12-16-17", 0.34, 0.03),
    "12-16-16": ("12-16-16", 0.45, 0.032),
    "18-16-17": ("18-16-17", 0.48, 0.03),
    "18-16-16": ("18-16-16", 0.65, 0.032),
    "24-16-17": ("24-16-17", 0.63, 0.03),
    "24-16-16": ("24-16-16", 0.85, 0.032),
    "30-16-17": ("30-16-17", 0.78, 0.03),
    "30-16-16": ("30-16-16", 1.05, 0.032),
    "36-16-17": ("36-16-17", 0.93, 0.03),
    "36-16-16": ("36-16-16", 1.26, 0.032),
    "42-16-17": ("42-16-17", 1.08, 0.03),
    "42-16-16": ("42-16-16", 1.47, 0.032),
    "48-16-17": ("48-16-17", 1.24, 0.03),
    "48-16-16": ("48-16-16", 1.67, 0.032),
    "54-16-17": ("54-16-17", 1.39, 0.03),
    "54-16-16": ("54-16-16", 1.88, 0.032),
}

MESH_100 = {
    "none": ("No mesh", 0, 0),
    "12-12-17": ("B12-12-17", 0.31, 0.02),
    "12-12-16": ("B12-12-16", 0.39, 0.028),
    "18-12-17": ("B18-12-17", 0.46, 0.02),
    "18-12-16": ("B18-12-16", 0.57, 0.028),
    "24-12-17": ("B24-12-17", 0.60, 0.02),
    "24-12-16": ("B24-12-16", 0.76, 0.028),
    "30-12-17": ("B30-12-17", 0.75, 0.02),
    "30-12-16": ("B30-12-16", 0.94, 0.028),
    "36-12-17": ("B36-12-17", 0.90, 0.02),
    "36-12-16": ("B36-12-16", 1.12, 0.028),
    "42-12-17": ("B42-12-17", 1.05, 0.02),
    "42-12-16": ("B42-12-16", 1.31, 0.028),
    "48-12-17": ("B48-12-17", 1.20, 0.02),
    "48-12-16": ("B48-12-16", 1.49, 0.028),
    "54-12-17": ("B54-12-17", 1.34, 0.02),
    "54-12-16": ("B54-12-16", 1.68, 0.028),
}

MESH_120 = {
    "none": ("No mesh", 0, 0),
    "18-10-17": ("18-10-17", 0.42, 0.02),
    "18-10-16": ("18-10-16", 0.57, 0.02),
    "24-10-17": ("24-10-17", 0.56, 0.02),
    "24-10-16": ("24-10-16", 0.75, 0.02),
    "30-10-17": ("30-10-17", 0.69, 0.02),
    "30-10-16": ("30-10-16", 0.93, 0.02),
    "36-10-17": ("36-10-17", 0.83, 0.02),
    "36-10-16": ("36-10-16", 1.12, 0.02),
    "42-10-17": ("42-10-17", 0.97, 0.02),
    "42-10-16": ("42-10-16", 1.30, 0.02),
    "48-10-17": ("48-10-17", 1.10, 0.02),
    "48-10-16": ("48-10-16", 1.49, 0.02),
    "54-10-17": ("54-10-17", 1.24, 0.02),
    "54-10-16": ("54-10-16", 1.67, 0.02),
}

MESH_150 = {
    "none": ("No mesh", 0, 0),
    "18-8-16": ("18-8-16", 0.53, 0),
    "24-8-16": ("24-8-16", 0.69, 0),
    "30-8-16": ("30-8-16", 0.86, 0),
    "36-8-16": ("36-8-16", 1.03, 0),
    "36-8-17": ("36-8-17", 0.78, 0),
    "42-8-16": ("42-8-16", 1.20, 0),
    "42-8-17": ("42-8-17", 0.91, 0),
    "48-8-16": ("48-8-16", 1.37, 0),
    "48-8-17": ("48-8-17", 1.03, 0),
    "54-8-16": ("54-8-16", 1.54, 0),
    "54-8-17": ("54-8-17", 1.16, 0),
}

OG150_BASE_TABLE = [
    (12, 2.30), (14, 2.49), (16, 2.69), (18, 2.88), (20, 3.08),
    (22, 3.28), (24, 3.47), (26, 3.67), (28, 3.86), (30, 4.06),
    (32, 4.26), (34, 4.45), (36, 4.65), (38, 4.84), (40, 5.04),
    (42, 5.24), (44, 5.43), (46, 5.63), (48, 5.82), (50, 6.02),
    (52, 6.22), (54, 6.41), (56, 6.61), (58, 6.80), (60, 7.00),
]

BELT_WEIGHT_DEFINITIONS = {
    "ofe1": {"mesh": None, "thickness_in": 0.50, "minimum_width": 6},
    "ofe2": {"mesh": None, "thickness_in": 0.50, "minimum_width": 6},
    "og075": {"mesh": MESH_075, "thickness_in": 0.4375, "minimum_width": 6},
    "og100": {"mesh": MESH_100, "thickness_in": 0.50, "minimum_width": 6},
    "og120": {"mesh": MESH_120, "thickness_in": 0.625, "minimum_width": 6},
    "og150": {
        "mesh": MESH_150,
        "thickness_in": 0.625,
        "minimum_width": 12,
        "maximum_width": 60,
    },
    "srog075": {
        "mesh": MESH_075,
        "thickness_in": 0.4375,
        "minimum_width": 8,
        "center_link": True,
    },
}


def interpolate_table(table, width):
    if width < table[0][0] or width > table[-1][0]:
        raise ValueError(
            "The 1.5-inch Omni-Grid width must be between 12 and 60 inches."
        )

    for index, row in enumerate(table):
        if width == row[0]:
            return row[1]

        if width < row[0]:
            width1, weight1 = table[index - 1]
            width2, weight2 = row
            return weight1 + ((width - width1) / (width2 - width1)) * (
                weight2 - weight1
            )

    return table[-1][1]


def belt_weight_per_foot(belt_type, width_in, mesh_key, center_link_in):
    definition = BELT_WEIGHT_DEFINITIONS[belt_type]
    mesh_table = definition.get("mesh")
    mesh = mesh_table.get(mesh_key) if mesh_table else None

    if mesh_table and mesh is None:
        raise ValueError("Invalid mesh selection.")

    mesh_weight = mesh[1] if mesh else 0
    allowance = mesh[2] if mesh else 0

    if belt_type == "ofe1":
        return (((width_in - 0.97) / 12) * 3.10) + 0.04

    if belt_type == "ofe2":
        return (((width_in - 0.97) / 12) * 3.35) + 0.04

    if belt_type == "og075":
        base = (0.008396 * width_in * 16) + (0.021 * 32) + 0.04
        overlay = mesh_weight * max(0, width_in - 2.224) / 12
        return base + overlay + allowance

    if belt_type == "og100":
        base = (0.008396 * width_in * 11.1) + (0.0348 * 22.2)
        overlay = mesh_weight * max(0, width_in - 2.5) / 12
        return base + overlay + allowance + (0.04 if mesh_weight > 0 else 0)

    if belt_type == "og120":
        base = (0.009759 * width_in * 10) + (0.0785 * 20) + 0.054
        overlay = mesh_weight * max(0, width_in - 3) / 12
        return base + overlay + allowance

    if belt_type == "og150":
        base = interpolate_table(OG150_BASE_TABLE, width_in)
        conveying_surface_ft = max(0, width_in - 2.75) / 12
        return base + mesh_weight * conveying_surface_ft

    if belt_type == "srog075":
        usable_center = max(0, min(center_link_in, width_in - 1.668))
        base = (
            (0.008396 * width_in * 16)
            + (0.021 * 16)
            + (0.027 * 16)
            + (0.0348 * 16)
            + 0.04
        )
        center_mesh = mesh_weight * max(0, usable_center - 1.668) / 12
        outer_mesh = (
            mesh_weight
            * (16 / 12)
            * max(0, width_in - usable_center - 1.668)
            / 12
        )
        return (
            base
            + center_mesh
            + outer_mesh
            + (allowance + 0.04 if mesh_weight > 0 else 0)
        )

    raise ValueError("Unsupported belt type.")


def excel_ceiling(value, significance=1):
    if not math.isfinite(value):
        return 0
    return math.ceil(value / significance) * significance


def excel_floor(value, significance=1):
    if not math.isfinite(value):
        return 0
    return math.floor(value / significance) * significance


def build_diameter_table(belt_thickness_in):
    table = []
    diameter = PACKING["pipe_diameter_in"] + 2 * belt_thickness_in
    accumulated_length = 0

    for _ in range(34):
        accumulated_length += math.pi * (diameter - belt_thickness_in) / 12
        diameter += 1
        table.append(
            {"footage": accumulated_length, "diameter_in": diameter}
        )

    return table


def diameter_lookup(table, footage):
    if not math.isfinite(footage) or footage <= 0:
        return 0

    selected = table[0]["diameter_in"]

    for row in table:
        if row["footage"] <= footage:
            selected = row["diameter_in"]
        else:
            break

    return selected


def calculate_crate_weight(length_in, width_in, height_in):
    top_bottom = excel_ceiling(length_in * width_in * 2 / 144, 1)
    short_sides = excel_ceiling(width_in * height_in * 2 / 144, 1)
    long_sides = excel_ceiling(length_in * height_in * 2 / 144, 1)
    total_square_feet = top_bottom + short_sides + long_sides

    return {
        "total_square_feet": total_square_feet,
        "tare_lb": total_square_feet * PACKING["plywood_weight_lb_ft2"],
    }


def calculate_packing(width_in, length_ft, belt_thickness_in):
    diameter_table = build_diameter_table(belt_thickness_in)
    standard_roll_diameter = diameter_lookup(
        diameter_table, PACKING["standard_roll_length_ft"]
    )

    if length_ft <= 100:
        rolls_across = 1
    elif length_ft <= 200:
        rolls_across = 2
    else:
        rolls_across = 2 if width_in * 3 > 89 else 3

    rolls_allowed_by_box_length = excel_floor(
        PACKING["maximum_box_length_in"] / width_in, 1
    )
    rolls_across = min(rolls_across, max(1, rolls_allowed_by_box_length))

    footage_per_main_box = rolls_across * 100
    simple_main_box_count = excel_floor(length_ft / footage_per_main_box, 1)
    unrounded_remainder = (
        length_ft - simple_main_box_count * footage_per_main_box
    )
    remainder_ratio = abs(unrounded_remainder) / footage_per_main_box

    calculated_main_box_count = simple_main_box_count + (
        1 if remainder_ratio >= 0.5 else 0
    )

    excess_check = calculated_main_box_count * footage_per_main_box > length_ft

    if excess_check:
        main_boxes_before_adjustment = calculated_main_box_count
    else:
        main_boxes_before_adjustment = (
            calculated_main_box_count - 1
            if calculated_main_box_count * footage_per_main_box - length_ft > 0
            else calculated_main_box_count
        )

    actual_leftover = (
        0
        if excess_check
        else length_ft
        - main_boxes_before_adjustment * footage_per_main_box
    )

    leftover_roll_count = actual_leftover / PACKING["standard_roll_length_ft"]
    rounded_leftover_roll_count = (
        excel_ceiling(leftover_roll_count, 1)
        if leftover_roll_count > 1
        else 0
    )

    main_box_quantity = main_boxes_before_adjustment

    if (
        rounded_leftover_roll_count > 2
        and rounded_leftover_roll_count < 5
        and rolls_across == 2
    ):
        main_box_quantity += 1
        actual_leftover = 0
    elif rounded_leftover_roll_count > 4 and rolls_across == 3:
        main_box_quantity += 1
        actual_leftover = 0

    box_types = []

    if main_box_quantity > 0:
        main_length_in = (
            rolls_across * width_in + PACKING["packing_clearance_in"]
        )

        main_width_in = (
            excel_ceiling(
                standard_roll_diameter + PACKING["packing_clearance_in"], 1
            )
            if length_ft == 50
            else excel_ceiling(
                2 * standard_roll_diameter + PACKING["packing_clearance_in"],
                1,
            )
        )

        main_height_in = excel_ceiling(
            standard_roll_diameter + PACKING["packing_clearance_in"], 1
        )

        crate = calculate_crate_weight(
    main_length_in, main_width_in, main_height_in
)

main_footage_total = length_ft - actual_leftover
main_footage_per_box = main_footage_total / main_box_quantity

box_types.append(
            {
                "quantity": main_box_quantity,
                "length_in": main_length_in,
                "width_in": main_width_in,
                "height_in": main_height_in,
                "tare_each_lb": crate["tare_lb"],
                "footage_per_box_ft": main_footage_per_box,
            }
        )

    remaining_rolls = actual_leftover / PACKING["standard_roll_length_ft"]

    if remaining_rolls > 0 and remaining_rolls < 3:
        if remaining_rolls <= 1:
            roll_count = excel_ceiling(remaining_rolls, 1)
            roll_length_ft = actual_leftover / roll_count
            roll_diameter_in = diameter_lookup(diameter_table, roll_length_ft)
            second_length_in = (
                roll_count * width_in + PACKING["packing_clearance_in"]
            )
            second_width_in = excel_ceiling(
                roll_diameter_in + PACKING["packing_clearance_in"], 1
            )
            second_height_in = second_width_in
        else:
            calculated_rolls_across = (
                1
                if actual_leftover <= 100
                else 2
                if actual_leftover <= 200
                else 3
            )
            maximum_rolls_across = excel_floor(
                PACKING["maximum_box_length_in"] / width_in, 1
            )
            actual_rolls_across = min(
                calculated_rolls_across, max(1, maximum_rolls_across)
            )
            second_length_in = (
                actual_rolls_across * width_in
                + PACKING["packing_clearance_in"]
            )
            second_width_in = (
                excel_ceiling(
                    standard_roll_diameter
                    + PACKING["packing_clearance_in"],
                    1,
                )
                if actual_leftover <= 50
                else excel_ceiling(
                    2 * standard_roll_diameter
                    + PACKING["packing_clearance_in"],
                    1,
                )
            )
            second_height_in = excel_ceiling(
                standard_roll_diameter + PACKING["packing_clearance_in"], 1
            )

        crate = calculate_crate_weight(
            second_length_in, second_width_in, second_height_in
        )

        box_types.append(
            {
                "quantity": 1,
                "length_in": second_length_in,
                "width_in": second_width_in,
                "height_in": second_height_in,
                "tare_each_lb": crate["tare_lb"],
                "footage_per_box_ft": actual_leftover,
            }
        )
packed_footage = sum(
    box["quantity"] * box["footage_per_box_ft"]
    for box in box_types
)

if abs(packed_footage - length_ft) > 0.01:
    raise ValueError(
        f"Packing footage mismatch: boxes contain "
        f"{packed_footage:.2f} ft, but the belt length is "
        f"{length_ft:.2f} ft."
    )
    total_quantity = sum(box["quantity"] for box in box_types)
    total_tare_lb = sum(
        box["quantity"] * box["tare_each_lb"] for box in box_types
    )

    return {
        "quantity": total_quantity,
        "total_tare_lb": total_tare_lb,
        "box_types": box_types,
    }


@app.route("/belt-weight-options", methods=["GET"])
def belt_weight_options():
    belt_type = request.args.get("belt_type", "").strip()

    if belt_type not in BELT_WEIGHT_DEFINITIONS:
        return jsonify({"error": "Invalid belt type."}), 400

    definition = BELT_WEIGHT_DEFINITIONS[belt_type]
    mesh_table = definition.get("mesh")

    return jsonify(
        {
            "meshes": (
                [
                    {"value": key, "label": values[0]}
                    for key, values in mesh_table.items()
                ]
                if mesh_table
                else []
            ),
            "center_link": bool(definition.get("center_link")),
        }
    )


@app.route("/belt-weight-packing", methods=["POST"])
def belt_weight_packing():
    data = request.get_json(silent=True) or {}

    try:
        belt_type = str(data.get("belt_type", "")).strip()
        width = float(data.get("width", 0))
        length = float(data.get("length", 0))
        width_unit = str(data.get("width_unit", "in")).strip()
        length_unit = str(data.get("length_unit", "ft")).strip()
        mesh_key = str(data.get("mesh", "none")).strip() or "none"
        center_link = float(data.get("center_link", 0) or 0)
        center_link_unit = str(
            data.get("center_link_unit", "in")
        ).strip()

        if belt_type not in BELT_WEIGHT_DEFINITIONS:
            raise ValueError("Please select a valid belt type.")

        if not math.isfinite(width) or width <= 0:
            raise ValueError("Please enter a valid belt width.")

        if not math.isfinite(length) or length <= 0:
            raise ValueError("Please enter a valid belt length.")

        if width_unit not in {"in", "mm"}:
            raise ValueError("Invalid width unit.")

        if length_unit not in {"ft", "m"}:
            raise ValueError("Invalid length unit.")

        if center_link_unit not in {"in", "mm"}:
            raise ValueError("Invalid center-link unit.")

        width_in = width / IN_TO_MM if width_unit == "mm" else width
        length_ft = length / FT_TO_M if length_unit == "m" else length
        center_link_in = (
            center_link / IN_TO_MM
            if center_link_unit == "mm"
            else center_link
        )

        definition = BELT_WEIGHT_DEFINITIONS[belt_type]

        if width_in < definition["minimum_width"]:
            raise ValueError(
                f"The selected belt requires a width of at least "
                f"{definition['minimum_width']} inches."
            )

        maximum_width = definition.get("maximum_width")
        if maximum_width and width_in > maximum_width:
            raise ValueError(
                f"The selected belt supports widths up to "
                f"{maximum_width} inches in this calculator."
            )

        if definition.get("center_link") and center_link_in >= width_in:
            raise ValueError(
                "Center-link coverage must be smaller than the overall "
                "belt width."
            )

        weight_lb_ft = belt_weight_per_foot(
            belt_type, width_in, mesh_key, center_link_in
        )

        if not math.isfinite(weight_lb_ft) or weight_lb_ft <= 0:
            raise ValueError(
                "The selected values did not produce a valid belt weight."
            )

        weight_kg_m = weight_lb_ft * LBFT_TO_KGM
        total_net_lb = weight_lb_ft * length_ft
        workbook_net_belt_lb = excel_ceiling(total_net_lb, 1)

        packing = calculate_packing(
            width_in, length_ft, definition["thickness_in"]
        )

        gross_lb = workbook_net_belt_lb + packing["total_tare_lb"]

        return jsonify(
            {
                "success": True,
                "weight_lb_ft": round(weight_lb_ft, 8),
                "weight_kg_m": round(weight_kg_m, 8),
                "net_weight_lb": workbook_net_belt_lb,
                "net_weight_kg": math.ceil(
                    workbook_net_belt_lb / 2.204622476
                ),
                "box_quantity": packing["quantity"],
                "box_weight_lb": round(packing["total_tare_lb"], 8),
                "box_weight_kg": round(
                    packing["total_tare_lb"] * LB_TO_KG, 8
                ),
                "gross_weight_lb": round(gross_lb, 8),
                "gross_weight_kg": math.ceil(gross_lb / 2.204622476),
                "box_types": packing["box_types"],
            }
        )

    except (TypeError, ValueError) as ex:
        return jsonify({"error": str(ex)}), 400

    except Exception:
        app.logger.exception("Belt weight and packing calculation failed.")
        return jsonify(
            {"error": "The server could not complete the calculation."}
        ), 500



# ==========================================================
# STACKER OUTFEED DRIVE RATIO CALCULATOR
# The sprocket search and ranking logic runs only on Render.
# ==========================================================

class StackerDriveRatioCalculator:
    @staticmethod
    def calculate(drive_rpm, belt_rpm, minimum_teeth, maximum_teeth):
        if not math.isfinite(drive_rpm) or drive_rpm <= 0:
            raise ValueError("Outfeed drive shaft RPM must be greater than zero.")
        if not math.isfinite(belt_rpm) or belt_rpm <= 0:
            raise ValueError("Belt outfeed shaft RPM must be greater than zero.")
        if int(minimum_teeth) != minimum_teeth or int(maximum_teeth) != maximum_teeth:
            raise ValueError("Minimum and maximum sprocket tooth counts must be whole numbers.")

        minimum_teeth = int(minimum_teeth)
        maximum_teeth = int(maximum_teeth)

        if minimum_teeth < 5 or maximum_teeth < 5:
            raise ValueError("Sprocket tooth counts must be at least 5.")
        if minimum_teeth > maximum_teeth:
            raise ValueError("Minimum sprocket teeth cannot be greater than maximum sprocket teeth.")
        if maximum_teeth > 200:
            raise ValueError("Maximum sprocket teeth cannot exceed 200.")

        required_ratio = belt_rpm / drive_rpm
        combinations = []

        for drive_teeth in range(minimum_teeth, maximum_teeth + 1):
            for driven_teeth in range(minimum_teeth, maximum_teeth + 1):
                ratio = drive_teeth / driven_teeth
                predicted_rpm = drive_rpm * ratio
                rpm_difference = predicted_rpm - belt_rpm
                speed_error_percent = (rpm_difference / belt_rpm) * 100

                combinations.append({
                    "drive_teeth": drive_teeth,
                    "driven_teeth": driven_teeth,
                    "ratio": ratio,
                    "predicted_rpm": predicted_rpm,
                    "rpm_difference": rpm_difference,
                    "speed_error_percent": speed_error_percent,
                    "absolute_error": abs(speed_error_percent),
                })

        combinations.sort(key=lambda item: (
            item["absolute_error"],
            item["drive_teeth"] + item["driven_teeth"],
            item["drive_teeth"],
        ))

        return {
            "required_ratio": required_ratio,
            "combinations": combinations[:10],
        }


@app.route("/stacker-drive-ratio", methods=["POST"])
def stacker_drive_ratio():
    data = request.get_json(silent=True) or {}
    required = ["drive_rpm", "belt_rpm", "minimum_teeth", "maximum_teeth"]
    missing = [field for field in required if field not in data or data[field] in (None, "")]

    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        result = StackerDriveRatioCalculator.calculate(
            float(data["drive_rpm"]),
            float(data["belt_rpm"]),
            float(data["minimum_teeth"]),
            float(data["maximum_teeth"]),
        )

        return jsonify({
            "success": True,
            "required_ratio": round(result["required_ratio"], 10),
            "combinations": [{
                "drive_teeth": item["drive_teeth"],
                "driven_teeth": item["driven_teeth"],
                "ratio": round(item["ratio"], 10),
                "predicted_rpm": round(item["predicted_rpm"], 10),
                "rpm_difference": round(item["rpm_difference"], 10),
                "speed_error_percent": round(item["speed_error_percent"], 10),
            } for item in result["combinations"]],
        })

    except (TypeError, ValueError) as ex:
        return jsonify({"error": str(ex)}), 400
    except Exception:
        app.logger.exception("Stacker drive ratio calculation failed.")
        return jsonify({"error": "The server could not complete the calculation."}), 500


# ==========================================================
# BELT SPROCKET CONVERSION CALCULATOR
# All conversion and drive-correction formulas run on Render.
# ==========================================================

class BeltSprocketConversionCalculator:
    @staticmethod
    def to_inches(value, unit):
        if unit == "in":
            return value
        if unit == "mm":
            return value / 25.4
        raise ValueError("Pitch unit must be 'in' or 'mm'.")

    @staticmethod
    def from_inches(value, unit):
        if unit == "in":
            return value
        if unit == "mm":
            return value * 25.4
        raise ValueError("Display unit must be 'in' or 'mm'.")

    @staticmethod
    def calculate(existing_pitch, existing_pitch_unit, existing_teeth,
                  new_pitch, new_pitch_unit, existing_speed,
                  speed_unit, tolerance, compare_range):

        if existing_pitch <= 0 or new_pitch <= 0:
            raise ValueError("Belt pitches must be greater than zero.")

        if existing_teeth <= 0 or int(existing_teeth) != existing_teeth:
            raise ValueError(
                "Existing sprocket teeth must be a positive whole number."
            )

        if existing_speed is not None and (
            not math.isfinite(existing_speed) or existing_speed < 0
        ):
            raise ValueError(
                "Existing belt speed must be zero or greater."
            )

        if speed_unit not in {"ft/min", "m/min"}:
            raise ValueError("Invalid belt speed unit.")

        tolerance = max(0.0, tolerance)
        compare_range = min(5, max(1, int(round(compare_range))))
        existing_teeth = int(existing_teeth)

        existing_pitch_inches = (
            BeltSprocketConversionCalculator.to_inches(
                existing_pitch, existing_pitch_unit
            )
        )
        new_pitch_inches = (
            BeltSprocketConversionCalculator.to_inches(
                new_pitch, new_pitch_unit
            )
        )

        original_travel_inches = (
            existing_pitch_inches * existing_teeth
        )
        ideal_teeth = original_travel_inches / new_pitch_inches
        recommended_teeth = max(1, round(ideal_teeth))
        new_travel_inches = new_pitch_inches * recommended_teeth
        relative_speed = new_travel_inches / original_travel_inches
        speed_error_percent = (relative_speed - 1) * 100

        display_unit = (
            existing_pitch_unit
            if existing_pitch_unit == new_pitch_unit
            else "in"
        )

        original_travel_display = (
            BeltSprocketConversionCalculator.from_inches(
                original_travel_inches, display_unit
            )
        )
        new_travel_display = (
            BeltSprocketConversionCalculator.from_inches(
                new_travel_inches, display_unit
            )
        )

        resulting_speed = None
        if existing_speed is not None and existing_speed > 0:
            resulting_speed = existing_speed * relative_speed

        minimum_teeth = max(
            1, math.floor(ideal_teeth) - compare_range
        )
        maximum_teeth = (
            math.ceil(ideal_teeth) + compare_range
        )

        comparisons = []

        for teeth in range(minimum_teeth, maximum_teeth + 1):
            travel_inches = new_pitch_inches * teeth
            ratio = travel_inches / original_travel_inches
            error_percent = (ratio - 1) * 100
            travel_display = (
                BeltSprocketConversionCalculator.from_inches(
                    travel_inches, display_unit
                )
            )

            if teeth == recommended_teeth:
                evaluation = "Recommended"
            elif abs(error_percent) <= tolerance:
                evaluation = "Within tolerance"
            else:
                evaluation = "Outside tolerance"

            comparisons.append({
                "teeth": teeth,
                "travel_per_revolution": travel_display,
                "relative_speed_percent": ratio * 100,
                "speed_error_percent": error_percent,
                "evaluation": evaluation,
                "recommended": teeth == recommended_teeth,
            })

        return {
            "original_travel": original_travel_display,
            "ideal_teeth": ideal_teeth,
            "recommended_teeth": recommended_teeth,
            "new_travel": new_travel_display,
            "relative_speed_percent": relative_speed * 100,
            "resulting_speed": resulting_speed,
            "speed_error_percent": speed_error_percent,
            "within_tolerance": abs(speed_error_percent) <= tolerance,
            "display_unit": display_unit,
            "speed_unit": speed_unit,
            "comparisons": comparisons,
        }

    @staticmethod
    def correction(existing_pitch, existing_pitch_unit, existing_teeth,
                   new_pitch, new_pitch_unit, selected_teeth,
                   method, method_inputs):

        if existing_pitch <= 0 or new_pitch <= 0:
            raise ValueError("Belt pitches must be greater than zero.")

        if existing_teeth <= 0 or int(existing_teeth) != existing_teeth:
            raise ValueError(
                "Existing sprocket teeth must be a positive whole number."
            )

        if selected_teeth <= 0 or int(selected_teeth) != selected_teeth:
            raise ValueError(
                "Selected belt sprocket teeth must be a positive whole number."
            )

        existing_pitch_inches = (
            BeltSprocketConversionCalculator.to_inches(
                existing_pitch, existing_pitch_unit
            )
        )
        new_pitch_inches = (
            BeltSprocketConversionCalculator.to_inches(
                new_pitch, new_pitch_unit
            )
        )

        original_travel_inches = (
            existing_pitch_inches * int(existing_teeth)
        )
        selected_travel_inches = (
            new_pitch_inches * int(selected_teeth)
        )

        correction_factor = (
            original_travel_inches / selected_travel_inches
        )
        selected_relative_speed = (
            selected_travel_inches / original_travel_inches
        )
        selected_error_percent = (
            selected_relative_speed - 1
        ) * 100

        result = {
            "method": method,
            "correction_factor": correction_factor,
            "selected_error_percent": selected_error_percent,
        }

        if method == "chain":
            driver_teeth = float(
                method_inputs.get("driver_teeth", 0)
            )
            driven_teeth = float(
                method_inputs.get("driven_teeth", 0)
            )

            if (
                driver_teeth <= 0 or driven_teeth <= 0
                or int(driver_teeth) != driver_teeth
                or int(driven_teeth) != driven_teeth
            ):
                raise ValueError(
                    "Chain sprocket teeth must be positive whole numbers."
                )

            driver_teeth = int(driver_teeth)
            driven_teeth = int(driven_teeth)

            current_ratio = driver_teeth / driven_teeth
            required_ratio = current_ratio * correction_factor

            theoretical_driver = required_ratio * driven_teeth
            recommended_driver = max(1, round(theoretical_driver))
            achieved_ratio_option_1 = (
                recommended_driver / driven_teeth
            )
            final_ratio_option_1 = (
                selected_relative_speed
                * (achieved_ratio_option_1 / current_ratio)
            )
            final_error_option_1 = (
                final_ratio_option_1 - 1
            ) * 100

            theoretical_driven = driver_teeth / required_ratio
            recommended_driven = max(1, round(theoretical_driven))
            achieved_ratio_option_2 = (
                driver_teeth / recommended_driven
            )
            final_ratio_option_2 = (
                selected_relative_speed
                * (achieved_ratio_option_2 / current_ratio)
            )
            final_error_option_2 = (
                final_ratio_option_2 - 1
            ) * 100

            result.update({
                "current_ratio": current_ratio,
                "required_ratio": required_ratio,
                "driver_teeth": driver_teeth,
                "driven_teeth": driven_teeth,
                "theoretical_driver": theoretical_driver,
                "recommended_driver": recommended_driver,
                "final_error_option_1": final_error_option_1,
                "theoretical_driven": theoretical_driven,
                "recommended_driven": recommended_driven,
                "final_error_option_2": final_error_option_2,
            })

        elif method == "pulley":
            driver_diameter = float(
                method_inputs.get("driver_diameter", 0)
            )
            driven_diameter = float(
                method_inputs.get("driven_diameter", 0)
            )

            if driver_diameter <= 0 or driven_diameter <= 0:
                raise ValueError(
                    "Pulley diameters must be positive values."
                )

            current_ratio = driver_diameter / driven_diameter
            required_ratio = current_ratio * correction_factor

            result.update({
                "current_ratio": current_ratio,
                "required_ratio": required_ratio,
                "required_driver": required_ratio * driven_diameter,
                "required_driven": driver_diameter / required_ratio,
            })

        elif method == "gearbox":
            gearbox_ratio = float(
                method_inputs.get("gearbox_ratio", 0)
            )

            if gearbox_ratio <= 0:
                raise ValueError(
                    "Gearbox reduction ratio must be positive."
                )

            result["required_gearbox_ratio"] = (
                gearbox_ratio / correction_factor
            )

        elif method == "vfd":
            current_frequency = float(
                method_inputs.get("current_frequency", 0)
            )

            if current_frequency <= 0:
                raise ValueError(
                    "VFD frequency must be positive."
                )

            result["required_frequency"] = (
                current_frequency * correction_factor
            )

        else:
            raise ValueError("Invalid correction method.")

        return result


@app.route("/belt-sprocket-conversion", methods=["POST"])
def belt_sprocket_conversion():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "calculate")).strip().lower()

    try:
        existing_speed_raw = data.get("existing_speed")
        existing_speed = (
            None
            if existing_speed_raw in (None, "")
            else float(existing_speed_raw)
        )

        common = {
            "existing_pitch": float(data.get("existing_pitch", 0)),
            "existing_pitch_unit": str(
                data.get("existing_pitch_unit", "")
            ).strip(),
            "existing_teeth": float(data.get("existing_teeth", 0)),
            "new_pitch": float(data.get("new_pitch", 0)),
            "new_pitch_unit": str(
                data.get("new_pitch_unit", "")
            ).strip(),
        }

        if action == "calculate":
            result = BeltSprocketConversionCalculator.calculate(
                **common,
                existing_speed=existing_speed,
                speed_unit=str(
                    data.get("speed_unit", "ft/min")
                ).strip(),
                tolerance=float(data.get("tolerance", 0)),
                compare_range=float(data.get("compare_range", 2)),
            )

        elif action == "correction":
            result = BeltSprocketConversionCalculator.correction(
                **common,
                selected_teeth=float(
                    data.get("selected_teeth", 0)
                ),
                method=str(
                    data.get("method", "")
                ).strip().lower(),
                method_inputs=data.get("method_inputs") or {},
            )

        else:
            raise ValueError("Invalid calculation action.")

        return jsonify({
            "success": True,
            "action": action,
            "result": result,
        })

    except (TypeError, ValueError) as ex:
        return jsonify({"error": str(ex)}), 400

    except Exception:
        app.logger.exception(
            "Belt sprocket conversion calculation failed."
        )
        return jsonify({
            "error": "The server could not complete the calculation."
        }), 500


# ==========================================================
# OVEN BAND CYLINDER FORCE CALCULATOR
# All force, unit-conversion, utilization, and margin logic
# runs only on Render.
# ==========================================================

class OvenBandForceCalculator:
    LBF_TO_N = 4.4482216153
    IN_TO_MM = 25.4
    BAR_TO_PA = 100000.0
    PSI_TO_PA = 6894.757293168

    @staticmethod
    def calculate(
        unit_system,
        num_cylinders,
        bore_diameter,
        rod_diameter,
        pressure,
        pressure_unit,
        stroke_direction,
        efficiency_percent,
        mechanical_ratio,
        band_strength,
    ):
        if unit_system not in {"imperial", "metric"}:
            raise ValueError("Invalid unit system.")

        if (
            num_cylinders < 1
            or int(num_cylinders) != num_cylinders
        ):
            raise ValueError(
                "Enter a whole number of cylinders greater than zero."
            )

        if not math.isfinite(bore_diameter) or bore_diameter <= 0:
            raise ValueError(
                "Cylinder bore diameter must be greater than zero."
            )

        if not math.isfinite(rod_diameter) or rod_diameter < 0:
            raise ValueError("Rod diameter cannot be negative.")

        if stroke_direction not in {"extension", "retraction"}:
            raise ValueError("Invalid force direction.")

        if (
            stroke_direction == "retraction"
            and rod_diameter >= bore_diameter
        ):
            raise ValueError(
                "Rod diameter must be smaller than the cylinder bore diameter."
            )

        if not math.isfinite(pressure) or pressure < 0:
            raise ValueError("Pressure cannot be negative.")

        if pressure_unit not in {"bar", "psi"}:
            raise ValueError("Invalid pressure unit.")

        if (
            not math.isfinite(efficiency_percent)
            or efficiency_percent <= 0
            or efficiency_percent > 100
        ):
            raise ValueError(
                "Efficiency must be between 1% and 100%."
            )

        if (
            not math.isfinite(mechanical_ratio)
            or mechanical_ratio <= 0
        ):
            raise ValueError(
                "Mechanical ratio must be greater than zero."
            )

        if band_strength is not None and (
            not math.isfinite(band_strength)
            or band_strength <= 0
        ):
            raise ValueError(
                "Band strength must be greater than zero."
            )

        bore_mm = (
            bore_diameter
            if unit_system == "metric"
            else bore_diameter * OvenBandForceCalculator.IN_TO_MM
        )
        rod_mm = (
            rod_diameter
            if unit_system == "metric"
            else rod_diameter * OvenBandForceCalculator.IN_TO_MM
        )

        pressure_pa = (
            pressure * OvenBandForceCalculator.BAR_TO_PA
            if pressure_unit == "bar"
            else pressure * OvenBandForceCalculator.PSI_TO_PA
        )

        piston_area_mm2 = math.pi * bore_mm ** 2 / 4
        rod_area_mm2 = math.pi * rod_mm ** 2 / 4

        effective_area_mm2 = (
            piston_area_mm2
            if stroke_direction == "extension"
            else piston_area_mm2 - rod_area_mm2
        )

        theoretical_force_n = (
            pressure_pa * effective_area_mm2 / 1_000_000
        )

        efficiency = efficiency_percent / 100
        adjusted_force_n = theoretical_force_n * efficiency

        total_force_n = (
            adjusted_force_n
            * int(num_cylinders)
            * mechanical_ratio
        )

        band_tension_n = total_force_n / 2

        comparison = None

        if band_strength is not None:
            band_strength_n = (
                band_strength
                if unit_system == "metric"
                else band_strength * OvenBandForceCalculator.LBF_TO_N
            )

            utilization_percent = (
                band_tension_n / band_strength_n
            ) * 100

            margin_n = band_strength_n - band_tension_n

            if utilization_percent <= 80:
                status = "safe"
                message = (
                    "Calculated band tension is within the entered "
                    "band strength."
                )
            elif utilization_percent <= 100:
                status = "warning"
                message = (
                    "Caution: calculated band tension is close to "
                    "the entered band strength."
                )
            else:
                status = "danger"
                message = (
                    "Warning: calculated band tension exceeds the "
                    "entered band strength."
                )

            comparison = {
                "status": status,
                "message": message,
                "utilization_percent": utilization_percent,
                "margin_n": margin_n,
                "margin_lbf": (
                    margin_n / OvenBandForceCalculator.LBF_TO_N
                ),
            }

        return {
            "piston_area_mm2": piston_area_mm2,
            "effective_area_mm2": effective_area_mm2,
            "theoretical_force_n": theoretical_force_n,
            "adjusted_force_n": adjusted_force_n,
            "total_force_n": total_force_n,
            "band_tension_n": band_tension_n,
            "comparison": comparison,
        }


@app.route("/oven-band-force", methods=["POST"])
def oven_band_force():
    data = request.get_json(silent=True) or {}

    required_fields = [
        "unit_system",
        "num_cylinders",
        "bore_diameter",
        "rod_diameter",
        "pressure",
        "pressure_unit",
        "stroke_direction",
        "efficiency_percent",
        "mechanical_ratio",
    ]

    missing = [
        field for field in required_fields
        if field not in data or data[field] in (None, "")
    ]

    if missing:
        return jsonify({
            "error": f"Missing fields: {', '.join(missing)}"
        }), 400

    try:
        band_strength_raw = data.get("band_strength")

        band_strength = (
            None
            if band_strength_raw in (None, "")
            else float(band_strength_raw)
        )

        result = OvenBandForceCalculator.calculate(
            unit_system=str(data["unit_system"]).strip(),
            num_cylinders=float(data["num_cylinders"]),
            bore_diameter=float(data["bore_diameter"]),
            rod_diameter=float(data["rod_diameter"]),
            pressure=float(data["pressure"]),
            pressure_unit=str(data["pressure_unit"]).strip(),
            stroke_direction=str(
                data["stroke_direction"]
            ).strip(),
            efficiency_percent=float(
                data["efficiency_percent"]
            ),
            mechanical_ratio=float(data["mechanical_ratio"]),
            band_strength=band_strength,
        )

        return jsonify({
            "success": True,
            "piston_area_mm2": round(
                result["piston_area_mm2"], 10
            ),
            "effective_area_mm2": round(
                result["effective_area_mm2"], 10
            ),
            "theoretical_force_n": round(
                result["theoretical_force_n"], 10
            ),
            "adjusted_force_n": round(
                result["adjusted_force_n"], 10
            ),
            "total_force_n": round(
                result["total_force_n"], 10
            ),
            "band_tension_n": round(
                result["band_tension_n"], 10
            ),
            "comparison": result["comparison"],
        })

    except (TypeError, ValueError) as ex:
        return jsonify({"error": str(ex)}), 400

    except Exception:
        app.logger.exception(
            "Oven band force calculation failed."
        )
        return jsonify({
            "error": "The server could not complete the calculation."
        }), 500



# ==========================================================
# BELT TURN RATIO CALCULATOR
# The engineering formula runs only on Render.
# The website sends only the two pitch inputs and receives
# the calculated turn ratio.
# ==========================================================

class BeltTurnRatioCalculator:
    @staticmethod
    def calculate(extended_pitch, compressed_pitch):
        if not math.isfinite(extended_pitch) or extended_pitch <= 0:
            raise ValueError("Extended pitch must be greater than zero.")

        if not math.isfinite(compressed_pitch) or compressed_pitch <= 0:
            raise ValueError("Compressed pitch must be greater than zero.")

        if compressed_pitch >= extended_pitch:
            raise ValueError(
                "Compressed pitch must be smaller than extended pitch."
            )

        turn_ratio = compressed_pitch / (
            extended_pitch - compressed_pitch
        )

        if not math.isfinite(turn_ratio) or turn_ratio <= 0:
            raise ValueError(
                "The entered pitches did not produce a valid turn ratio."
            )

        return turn_ratio


@app.route("/turn-ratio", methods=["POST"])
def turn_ratio():
    data = request.get_json(silent=True) or {}

    required_fields = ["extended_pitch", "compressed_pitch"]

    missing = [
        field
        for field in required_fields
        if field not in data or data[field] in (None, "")
    ]

    if missing:
        return jsonify({
            "error": f"Missing fields: {', '.join(missing)}"
        }), 400

    try:
        result = BeltTurnRatioCalculator.calculate(
            extended_pitch=float(data["extended_pitch"]),
            compressed_pitch=float(data["compressed_pitch"]),
        )

        return jsonify({
            "success": True,
            "turn_ratio": round(result, 4),
        })

    except (TypeError, ValueError) as ex:
        return jsonify({"error": str(ex)}), 400

    except Exception:
        app.logger.exception(
            "Belt turn ratio calculation failed."
        )
        return jsonify({
            "error": "The server could not complete the calculation."
        }), 500


# ==========================================================
# SIDE DRIVE SPIRAL DRIVE SIZING CALCULATOR
# All spiral, return-path, torque, power, drive-count and
# shaft-sizing calculations run only on Render.
# ==========================================================

class SideDriveSpiralSizingCalculator:
    @staticmethod
    def calculate(
        direction,
        production_rate,
        belt_width,
        belt_weight,
        turn_ratio,
        rail_friction,
        infeed_length,
        outfeed_length,
        tiers,
        tier_spacing,
        dwell_time,
        sprocket_radius,
        acceleration_time,
        allowable_tension,
        take_up_tension,
        return_length,
        return_friction,
        additional_return_resistance,
        drive_efficiency,
        service_factor,
        curve_drag_coefficient,
        allowable_shaft_shear_stress,
    ):
        numeric_values = {
            "Production rate": production_rate,
            "Belt width": belt_width,
            "Belt weight": belt_weight,
            "Turn ratio": turn_ratio,
            "Rail friction": rail_friction,
            "Infeed length": infeed_length,
            "Outfeed length": outfeed_length,
            "Number of tiers": tiers,
            "Tier spacing": tier_spacing,
            "Dwell time": dwell_time,
            "Sprocket radius": sprocket_radius,
            "Acceleration time": acceleration_time,
            "Allowable belt tension": allowable_tension,
            "Take-up tension": take_up_tension,
            "Return length": return_length,
            "Return friction": return_friction,
            "Additional return resistance": additional_return_resistance,
            "Drive efficiency": drive_efficiency,
            "Service factor": service_factor,
            "Curve-drag coefficient": curve_drag_coefficient,
            "Allowable shaft shear stress": allowable_shaft_shear_stress,
        }

        for label, value in numeric_values.items():
            if not math.isfinite(value):
                raise ValueError(f"{label} must be a valid number.")

        if direction not in {"up", "down"}:
            raise ValueError("Running direction must be 'up' or 'down'.")

        positive_values = {
            "Production rate": production_rate,
            "Belt width": belt_width,
            "Belt weight": belt_weight,
            "Turn ratio": turn_ratio,
            "Rail friction": rail_friction,
            "Number of tiers": tiers,
            "Tier spacing": tier_spacing,
            "Dwell time": dwell_time,
            "Sprocket radius": sprocket_radius,
            "Acceleration time": acceleration_time,
            "Allowable belt tension": allowable_tension,
            "Drive efficiency": drive_efficiency,
            "Service factor": service_factor,
            "Allowable shaft shear stress": allowable_shaft_shear_stress,
        }

        for label, value in positive_values.items():
            if value <= 0:
                raise ValueError(f"{label} must be greater than zero.")

        if int(tiers) != tiers:
            raise ValueError("Number of tiers must be a whole number.")

        tiers = int(tiers)

        if drive_efficiency > 1:
            raise ValueError("Drive efficiency must be between 0 and 1.")

        nonnegative_values = {
            "Infeed length": infeed_length,
            "Outfeed length": outfeed_length,
            "Take-up tension": take_up_tension,
            "Return length": return_length,
            "Return friction": return_friction,
            "Additional return resistance": additional_return_resistance,
            "Curve-drag coefficient": curve_drag_coefficient,
        }

        for label, value in nonnegative_values.items():
            if value < 0:
                raise ValueError(f"{label} cannot be negative.")

        g = 9.81

        inside_radius = turn_ratio * belt_width
        mean_radius = inside_radius + belt_width / 2
        circumference_per_tier = (
            inside_radius * 2 + belt_width * 2
        ) * math.pi

        helix_length = circumference_per_tier * tiers

        belt_length_infeed_to_outfeed = (
            helix_length + infeed_length + outfeed_length
        )

        if belt_length_infeed_to_outfeed <= 0:
            raise ValueError(
                "Calculated belt length from infeed to outfeed must be greater than zero."
            )

        product_mass_on_belt = (
            production_rate * dwell_time / 60
        )

        product_weight_per_meter = (
            product_mass_on_belt / belt_length_infeed_to_outfeed
        )

        moving_mass_per_meter = (
            belt_weight + product_weight_per_meter
        )

        belt_speed_m_per_min = (
            belt_length_infeed_to_outfeed / dwell_time
        )

        belt_speed_m_per_sec = belt_speed_m_per_min / 60

        helix_angle = math.atan(
            tier_spacing / (2 * math.pi * mean_radius)
        )

        friction_force = (
            rail_friction
            * moving_mass_per_meter
            * circumference_per_tier
            * g
        )

        vertical_lift_magnitude = (
            moving_mass_per_meter
            * circumference_per_tier
            * g
            * math.sin(helix_angle)
        )

        signed_vertical_force = (
            -vertical_lift_magnitude
            if direction == "down"
            else vertical_lift_magnitude
        )

        curve_drag_force = (
            moving_mass_per_meter
            * circumference_per_tier
            * g
            * curve_drag_coefficient
        )

        acceleration_force = (
            moving_mass_per_meter
            * circumference_per_tier
            * belt_speed_m_per_sec
            / acceleration_time
        )

        # Each tier is driven. The force generated in one tier therefore
        # does not accumulate into the next tier.
        spiral_force_per_tier = max(
            0.0,
            friction_force
            + signed_vertical_force
            + curve_drag_force
            + acceleration_force,
        )

        # The return belt is assumed empty and level.
        return_friction_force = (
            return_friction
            * belt_weight
            * g
            * return_length
        )

        entry_tension = (
            take_up_tension
            + return_friction_force
            + additional_return_resistance
        )

        # Return-path tension acts only on the first tier.
        # Tension is reset near zero after each driven sprocket.
        first_tier_tension = (
            entry_tension + spiral_force_per_tier
        )

        subsequent_tier_tension = spiral_force_per_tier

        maximum_tier_tension = max(
            first_tier_tension,
            subsequent_tier_tension,
        )

        drive_count = max(
            1,
            math.ceil(maximum_tier_tension / allowable_tension),
        )

        tension_per_drive_location = (
            maximum_tier_tension / drive_count
        )

        tension_utilization_percent = (
            tension_per_drive_location
            / allowable_tension
            * 100
        )

        total_running_force_across_all_tiers = (
            first_tier_tension
            + subsequent_tier_tension * max(0, tiers - 1)
        )

        total_sprocket_torque = (
            total_running_force_across_all_tiers
            * sprocket_radius
        )

        running_torque_per_drive_shaft = (
            total_sprocket_torque / drive_count
        )

        design_torque_per_drive_shaft = (
            running_torque_per_drive_shaft
            * service_factor
        )

        sprocket_circumference = (
            2 * math.pi * sprocket_radius
        )

        sprocket_rpm = (
            belt_speed_m_per_min / sprocket_circumference
        )

        angular_speed = sprocket_rpm * 2 * math.pi / 60

        power_per_drive_kw = (
            design_torque_per_drive_shaft
            * angular_speed
            / drive_efficiency
            / 1000
        )

        shaft_diameter_mm = (
            16
            * design_torque_per_drive_shaft
            * 1000
            / (
                math.pi
                * allowable_shaft_shear_stress
            )
        ) ** (1 / 3)

        return {
            "drive_count": drive_count,
            "design_torque_per_drive_nm": design_torque_per_drive_shaft,
            "power_per_drive_kw": power_per_drive_kw,
            "sprocket_rpm": sprocket_rpm,
            "minimum_shaft_diameter_mm": shaft_diameter_mm,
            "maximum_tier_tension_n": maximum_tier_tension,
            "tension_per_drive_location_n": tension_per_drive_location,
            "tension_utilization_percent": tension_utilization_percent,
            "entry_tension_n": entry_tension,
            "return_friction_force_n": return_friction_force,
            "spiral_force_per_tier_n": spiral_force_per_tier,
            "first_tier_tension_n": first_tier_tension,
            "subsequent_tier_tension_n": subsequent_tier_tension,
            "belt_speed_m_per_min": belt_speed_m_per_min,
        }


@app.route("/side-drive-sizing", methods=["POST"])
def side_drive_sizing():
    data = request.get_json(silent=True) or {}

    required_fields = [
        "direction",
        "production_rate",
        "belt_width",
        "belt_weight",
        "turn_ratio",
        "rail_friction",
        "infeed_length",
        "outfeed_length",
        "tiers",
        "tier_spacing",
        "dwell_time",
        "sprocket_radius",
        "acceleration_time",
        "allowable_tension",
        "take_up_tension",
        "return_length",
        "return_friction",
        "additional_return_resistance",
        "drive_efficiency",
        "service_factor",
        "curve_drag_coefficient",
        "allowable_shaft_shear_stress",
    ]

    missing = [
        field
        for field in required_fields
        if field not in data or data[field] in (None, "")
    ]

    if missing:
        return jsonify({
            "error": f"Missing fields: {', '.join(missing)}"
        }), 400

    try:
        result = SideDriveSpiralSizingCalculator.calculate(
            direction=str(data["direction"]).strip().lower(),
            production_rate=float(data["production_rate"]),
            belt_width=float(data["belt_width"]),
            belt_weight=float(data["belt_weight"]),
            turn_ratio=float(data["turn_ratio"]),
            rail_friction=float(data["rail_friction"]),
            infeed_length=float(data["infeed_length"]),
            outfeed_length=float(data["outfeed_length"]),
            tiers=float(data["tiers"]),
            tier_spacing=float(data["tier_spacing"]),
            dwell_time=float(data["dwell_time"]),
            sprocket_radius=float(data["sprocket_radius"]),
            acceleration_time=float(data["acceleration_time"]),
            allowable_tension=float(data["allowable_tension"]),
            take_up_tension=float(data["take_up_tension"]),
            return_length=float(data["return_length"]),
            return_friction=float(data["return_friction"]),
            additional_return_resistance=float(
                data["additional_return_resistance"]
            ),
            drive_efficiency=float(data["drive_efficiency"]),
            service_factor=float(data["service_factor"]),
            curve_drag_coefficient=float(
                data["curve_drag_coefficient"]
            ),
            allowable_shaft_shear_stress=float(
                data["allowable_shaft_shear_stress"]
            ),
        )

        return jsonify({
            "success": True,
            **{
                key: (
                    value
                    if isinstance(value, int)
                    else round(value, 10)
                )
                for key, value in result.items()
            },
        })

    except (TypeError, ValueError) as ex:
        return jsonify({"error": str(ex)}), 400

    except Exception:
        app.logger.exception(
            "Side drive spiral sizing calculation failed."
        )
        return jsonify({
            "error": "The server could not complete the calculation."
        }), 500

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
