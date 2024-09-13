from flask import Flask, request, jsonify
from dotenv import load_dotenv
from pymongo import MongoClient
from datetime import datetime, timedelta
import os

app = Flask(__name__)

# Load environment variables from the .env file
load_dotenv()

# Get the MongoDB connection string from the environment variables
mongo_uri = os.getenv('mongo_uri')

client = MongoClient(mongo_uri)
db = client["MedMinion"]

# Collections
doctor_availability_collection = db['Doctors Availability Collection']
delhi_ncr_doctors_collection = db['Delhi_ncr_doctors']
appointments_collection = db['Appointments_Collection']

# Helper function to get the day of the week as a string (Monday, Tuesday, etc.)
def get_day_of_week(date):
    return date.strftime('%A')

@app.route('/fetch_departments', methods=['GET'])
def fetch_departments():
    departments = delhi_ncr_doctors_collection.distinct('Speciality/Domain')
    return jsonify(departments)

@app.route('/fetch_locations', methods=['GET'])
def fetch_locations():
    department = request.args.get('department')
    locations = delhi_ncr_doctors_collection.distinct('Clinic Address', {"Speciality/Domain": department})
    return jsonify(locations)

@app.route('/fetch_doctors', methods=['GET'])
def fetch_doctors():
    department = request.args.get('department')
    location = request.args.get('location')
    doctors = delhi_ncr_doctors_collection.find({"Speciality/Domain": department, "Clinic Address": location},
                                                {"Doctors Name": 1, "Contact": 1, "_id": 0})
    return jsonify(list(doctors))

@app.route('/fetch_doctor_availability', methods=['GET'])
def fetch_doctor_availability():
    doctor_name = request.args.get('doctor_name')
    today = datetime.today()
    availability = []

    # Fetch the doctor's availability record
    doctor_schedule = doctor_availability_collection.find_one({"doctor_name": {"$regex": doctor_name.strip(), "$options": "i"}})

    if not doctor_schedule or "availability" not in doctor_schedule:
        return jsonify({"error": "Doctor schedule not found or no availability field"}), 404

    doctor_availability = doctor_schedule["availability"]

    # Loop through the next 7 days
    for i in range(7):
        day = today + timedelta(days=i)
        day_name = get_day_of_week(day)
        date = day.strftime('%Y-%m-%d')

        available_times = []

        # If the day is in the availability dictionary, check for available time slots
        if day_name in doctor_availability:
            time_slots = doctor_availability[day_name]

            for time_slot, slots in time_slots.items():
                if isinstance(slots, list) and any(slot == 0 for slot in slots):  # Check if at least one slot is available (0)
                    available_times.append(time_slot)

        # Append availability if there are available time slots
        if available_times:
            availability.append({
                "date": date,
                "day_name": day_name,
                "available_times": available_times
            })

    return jsonify(availability)

@app.route('/book_appointment', methods=['POST'])
def book_appointment():
    data = request.json
    patient_id = data.get('patient_id')
    doctor_name = data.get('doctor_name')
    appointment_date = data.get('appointment_date')
    appointment_time = data.get('appointment_time')
    clinic_location = data.get('clinic_location')
    doctor_contact = data.get('doctor_contact')

    try:
        # Insert the appointment details
        appointment = {
            "patient_id": patient_id,
            "doctor_name": doctor_name,
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
            "status": "Scheduled",
            "clinic_location": clinic_location,
            "doctor_contact": doctor_contact
        }
        appointments_collection.insert_one(appointment)

        # Update doctor's availability
        day_name = get_day_of_week(datetime.strptime(appointment_date, '%Y-%m-%d'))
        doctor_schedule = doctor_availability_collection.find_one({"doctor_name": {"$regex": doctor_name.strip(), "$options": "i"}})
        if doctor_schedule and "availability" in doctor_schedule and day_name in doctor_schedule["availability"]:
            time_slots = doctor_schedule["availability"][day_name]
            if appointment_time in time_slots and isinstance(time_slots[appointment_time], list):
                if 0 in time_slots[appointment_time]:
                    for i in range(len(time_slots[appointment_time])):
                        if time_slots[appointment_time][i] == 0:
                            time_slots[appointment_time][i] = 1
                            break
                    doctor_availability_collection.update_one(
                        {"doctor_name": {"$regex": doctor_name.strip(), "$options": "i"}},
                        {"$set": {f"availability.{day_name}.{appointment_time}": time_slots[appointment_time]}}
                    )
                    return jsonify({"message": "Your appointment is successfully booked!"})
                else:
                    return jsonify({"error": "The selected time slot is already full."}), 400
            else:
                return jsonify({"error": "The selected time slot is not available."}), 400
        else:
            return jsonify({"error": "Doctor schedule not found or the day is not available."}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/reschedule_appointment_flow', methods=['POST'])
def reschedule_appointment_flow():
    data = request.json
    patient_id = data['patient_id']

    # Fetch all scheduled appointments
    appointments = list(appointments_collection.find({"patient_id": patient_id, "status": "Scheduled"}))
    
    if not appointments:
        return jsonify({"error": "No scheduled appointments found."}), 404

    selected_appointment = appointments[0]  # Assuming we take the first appointment for simplicity

    doctor_name = selected_appointment['doctor_name']
    old_appointment_date = selected_appointment['appointment_date']
    old_appointment_time = selected_appointment['appointment_time']

    # Fetch availability
    availability = fetch_doctor_availability()

    if not availability:
        return jsonify({"error": "No availability found."}), 404

    new_appointment_date = data['new_appointment_date']
    new_appointment_time = data['new_appointment_time']

    confirmation = book_appointment({
        'patient_id': patient_id,
        'doctor_name': doctor_name,
        'appointment_date': new_appointment_date,
        'appointment_time': new_appointment_time,
        'clinic_location': selected_appointment['clinic_location'],
        'doctor_contact': selected_appointment['doctor_contact']
    })

    if confirmation.json['message'] == "Your appointment is successfully booked!":
        appointments_collection.delete_one({"_id": selected_appointment['_id']})

        # Free up old time slot
        doctor_schedule = doctor_availability_collection.find_one({"doctor_name": {"$regex": doctor_name.strip(), "$options": "i"}})
        old_day_name = get_day_of_week(datetime.strptime(old_appointment_date, '%Y-%m-%d'))

        if doctor_schedule and "availability" in doctor_schedule and old_day_name in doctor_schedule["availability"]:
            time_slots = doctor_schedule["availability"][old_day_name]

            if old_appointment_time in time_slots and isinstance(time_slots[old_appointment_time], list):
                for i in range(len(time_slots[old_appointment_time])):
                    if time_slots[old_appointment_time][i] == 1:
                        time_slots[old_appointment_time][i] = 0
                        break

            doctor_availability_collection.update_one(
                {"doctor_name": {"$regex": doctor_name.strip(), "$options": "i"}},
                {"$set": {f"availability.{old_day_name}.{old_appointment_time}": time_slots[old_appointment_time]}}
            )
        return jsonify({"message": "Appointment rescheduled."})

    return jsonify({"error": "Failed to reschedule appointment."}), 500

@app.route('/cancel_appointment_flow', methods=['POST'])
def cancel_appointment_flow():
    data = request.json
    patient_id = data['patient_id']

    # Fetch scheduled appointments
    appointments = list(appointments_collection.find({"patient_id": patient_id, "status": "Scheduled"}))
    
    if not appointments:
        return jsonify({"error": "No scheduled appointments found."}), 404

    selected_appointment = appointments[0]

    doctor_name = selected_appointment['doctor_name']
    appointment_date = selected_appointment['appointment_date']
    appointment_time = selected_appointment['appointment_time']

    # Update appointment status
    appointments_collection.update_one(
        {"_id": selected_appointment['_id']},
        {"$set": {"status": "Canceled"}}
    )

    # Free up time slot
    doctor_schedule = doctor_availability_collection.find_one({"doctor_name": {"$regex": doctor_name.strip(), "$options": "i"}})
    day_name = get_day_of_week(datetime.strptime(appointment_date, '%Y-%m-%d'))

    if doctor_schedule and "availability" in doctor_schedule and day_name in doctor_schedule["availability"]:
        time_slots = doctor_schedule["availability"][day_name]

        if appointment_time in time_slots and isinstance(time_slots[appointment_time], list):
            for i in range(len(time_slots[appointment_time])):
                if time_slots[appointment_time][i] == 1:
                    time_slots[appointment_time][i] = 0
                    break

        doctor_availability_collection.update_one(
            {"doctor_name": {"$regex": doctor_name.strip(), "$options": "i"}},
            {"$set": {f"availability.{day_name}.{appointment_time}": time_slots[appointment_time]}}
        )
    return jsonify({"message": "Appointment canceled successfully."})

@app.route('/check_availability_for_bookings', methods=['GET'])
def check_availability_for_bookings():
    doctor_name = request.args.get('doctor_name')
    appointment_date = request.args.get('appointment_date')
    appointment_time = request.args.get('appointment_time')

    doctor_schedule = doctor_availability_collection.find_one({"doctor_name": {"$regex": doctor_name.strip(), "$options": "i"}})
    day_name = get_day_of_week(datetime.strptime(appointment_date, '%Y-%m-%d'))

    if doctor_schedule and "availability" in doctor_schedule and day_name in doctor_schedule["availability"]:
        time_slots = doctor_schedule["availability"][day_name]

        if appointment_time in time_slots and isinstance(time_slots[appointment_time], list):
            available = 0 in time_slots[appointment_time]
            return jsonify({"available": available})

    return jsonify({"available": False})

if __name__ == '__main__':
    app.run(debug=True)
