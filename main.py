import json
import os
import pathlib
from datetime import datetime
from os.path import exists

import requests
from dateutil.parser import parse
from twilio.rest import Client

### CONFIGURATION
from config import RESTAURANT_ID, MIN_HOUR, MAX_HOUR, PARTY_SIZE, RESERVATION_FILENAME

# Your Account SID from twilio.com/console
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID")
# Your Auth Token from twilio.com/console
TWILIO_TOKEN = os.environ.get("TWILIO_ACCOUNT_TOKEN")
TO_PHONE_NUMBER = os.environ.get("TO_PHONE_NUMBER")
FROM_PHONE_NUMBER = os.environ.get("FROM_PHONE_NUMBER")
BEARER_TOKEN = os.environ.get("BEARER_TOKEN")

CURR_FILE_DIRECTORY = pathlib.Path(__file__).parent.resolve()

twilio_client = None
if TWILIO_TOKEN and TWILIO_SID and TO_PHONE_NUMBER and FROM_PHONE_NUMBER:
    twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)

headers = {
    'Content-Type': 'application/json',
    'Authorization': f"Bearer {BEARER_TOKEN}",
    'User-Agent': 'com.contextoptional.OpenTable/15.2.0.16; iPhone; iOS/15.1.1; 3.0;',
}


def notify_of_reservation(reservation):
    if not twilio_client:
        return
    body = f"Reservation for {reservation['restaurant']['name'][:20]} at {reservation['dateTime']} completed"
    print(f"Sending text message: {body}")
    message = twilio_client.messages.create(
        to=TO_PHONE_NUMBER,
        from_=FROM_PHONE_NUMBER,
        body=body)
    print(f"Sent text message: {message.sid}")


def get_availability_for_restaurant_id(id):
    today = datetime.now().strftime("%Y-%m-%dT%H:00")
    data = {
        "forceNextAvailable": "true",
        "includeNextAvailable": True,
        "availabilityToken": "eyJ2IjoyLCJtIjoxLCJwIjoxLCJzIjowLCJuIjowfQ",
        "dateTime": today,
        "requestTicket": "true",
        "allowPop": True,
        "attribution": {
            "partnerId": "84"
        },
        "partySize": PARTY_SIZE,
        "includeOffers": True,
        "requestPremium": "true",
        "requestDateMessages": True,
        "rids": [
            str(id)
        ],
        "requestAttributeTables": "true"
    }

    response = requests.put('https://mobile-api.opentable.com/api/v3/restaurant/availability', headers=headers,
                            data=json.dumps(data))
    return response.json()


def make_reservation_for_slot_response(slot):
    slot_time = slot['dateTime']
    slot_hash = slot['slotHash']
    data = {
        "partySize": PARTY_SIZE,
        "dateTime": slot_time,
        "selectedDiningArea": {
            "tableAttribute": "default",
            "diningAreaId": "1"
        },
        "hash": slot_hash,
        "attribution": {
            "partnerId": "84"
        }
    }
    print(f"Attempting to lock down reservation {slot_hash} at {slot_time}")
    response = requests.post(f'https://mobile-api.opentable.com/api/v1/reservation/{RESTAURANT_ID}/lock',
                             headers=headers,
                             data=json.dumps(data))
    lock = response.json()

    lock_id = lock['id']
    print(f"Successfully locked reservation with lock id {lock_id}")
    complete_reservation_data = {
        "diningFormOptIn": True,
        "partySize": PARTY_SIZE,
        "gpid": os.environ.get("OPENTABLE_GPID"),  # I don't know what this is or if its user specific
        "countryId": "US",
        "attribution": {
            "partnerId": "84"  # ¯\_(ツ)_/¯
        },
        "occasion": "anniversary",
        "loyaltyProgramOptIn": True,
        "optIns": {  # Not sure if this section is necessary
            "smsNotifications": {
                "reservationSms": True,
                "waitlistSms": False
            },
            "openTableDataSharing": {
                "businessPartners": True,
                "corporateGroup": True,
                "pointOfSale": True
            },
            "dataSharing": {
                "guestShare": True,
                "dinerProfileShare": True,
                "sync": True
            },
            "restaurantEmailMarketing": {
                "restaurantEmails": True
            },
            "emailNotifications": {
                "diningFeedback": True
            },
            "emailMarketing": {
                "newHot": False,
                "restaurantWeek": False,
                "spotlight": False,
                "product": False,
                "promotional": False,
                "insider": False,
                "dinersChoice": False
            }
        },
        "rewardTier": "GreatDeal",
        "hash": slot_hash,
        "points": 1000,  # Can I put an arbitrary value here? Lol
        "loadInvitations": False,
        "number": TO_PHONE_NUMBER,
        "notes": "",
        "slotAvailabilityToken": slot['token'],
        "selectedDiningArea": {
            "diningAreaId": "1",
            "tableAttribute": "default"
        },
        "lockId": lock_id,
        "dinerId": os.environ.get("OPENTABLE_DINERID"),
        "location": {
            "latitude": 40.74,  # I don't know if this matters. but I set it to a non specific location in NY
            "longitude": -73.98
        },
        "dateTime": slot_time
    }
    print(f"Attempting to complete reservation {lock_id}")
    final_booking_response = requests.post(
        f"https://mobile-api.opentable.com/api/v1/reservation/{RESTAURANT_ID}",
        headers=headers,
        data=json.dumps(complete_reservation_data))

    # This will return not JSON if it fails, although sometimes when it fails it actually makes the reservation
    # We'll write this for the happy path, but welcome PRs cleaning this up
    final_booking_data = final_booking_response.json()
    if RESERVATION_FILENAME:
        print("Saving reservation details to file...")
        with open(RESERVATION_FILENAME, 'w') as reservation_out:
            reservation_out.write(final_booking_response.text)
            reservation_out.close()
    print(f"Successfully made reservation {final_booking_data['id']} at {final_booking_data['restaurant']['name']}")
    print(
        f"Reservation token: {final_booking_data['token']} and confirmation number {final_booking_data['confirmationNumber']}")
    notify_of_reservation(final_booking_data)
    return final_booking_data


def run():
    try:
        print("Fetching availability")
        availability_response = get_availability_for_restaurant_id(RESTAURANT_ID)
        if 'suggestedAvailability' in availability_response:
            for day in availability_response['suggestedAvailability']:
                if 'timeslots' in day and len(day['timeslots']):
                    for slot in day['timeslots']:
                        slot_date = parse(slot['dateTime'])
                        # todo - sort available dates by order of preference. if a big batch gets released and you have
                        # 5pm to 11pm selected, you might get the 5pm even though the 8pm is preferable
                        if slot_date.hour >= MIN_HOUR and slot_date.hour <= MAX_HOUR:  # and slot_date.day >= MIN_DATE:
                            print(f"Found available time slot on {day['dateTime']}")
                            make_reservation_for_slot_response(slot)
                            return
            print("No availability found")
        else:
            print("Error with response, unknown format")
    except Exception as e:
        print(e)
        pass


if not RESERVATION_FILENAME or not exists(f"{CURR_FILE_DIRECTORY}/{RESERVATION_FILENAME}"):
    run()
else:
    print("Found an already existing reservation that was created. Delete ")
