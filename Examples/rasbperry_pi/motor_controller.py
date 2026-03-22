from gpiozero import PWMOutputDevice, DigitalOutputDevice
import time

# --- Pin Setup ---
# Motor A
ENA = PWMOutputDevice(12)  # PWM Speed Control
IN1 = DigitalOutputDevice(17) # Direction 1
IN2 = DigitalOutputDevice(27) # Direction 2

# Motor B
ENB = PWMOutputDevice(13)  # PWM Speed Control
IN3 = DigitalOutputDevice(22) # Direction 1
IN4 = DigitalOutputDevice(23) # Direction 2

def move_motors(velR, velL, t=None):
    """
    Core function to drive both motors.
    Speeds (velR, velL) should be between -1.0 (full reverse) and 1.0 (full forward).
    If t (seconds) is provided, the motors will run for that duration and stop.
    """
    # Clamp velocities to the safe -1.0 to 1.0 range
    velR = max(min(velR, 1.0), -1.0)
    velL = max(min(velL, 1.0), -1.0)

    # --- Right Motor Control ---
    if velR > 0:
        IN3.on()
        IN4.off()
        ENB.value = velR
    elif velR < 0:
        IN3.off()
        IN4.on()
        ENB.value = abs(velR)
    else:
        IN3.off()
        IN4.off()
        ENB.value = 0.0

    # --- Left Motor Control ---
    if velL > 0:
        IN1.on()
        IN2.off()
        ENA.value = velL
    elif velL < 0:
        IN1.off()
        IN2.on()
        ENA.value = abs(velL)
    else:
        IN1.off()
        IN2.off()
        ENA.value = 0.0

    # --- Handle optional time ---
    if t is not None:
        time.sleep(t)
        stop_motors()

def move_forward(vel):
    move_motors(vel, vel)

def move_back(vel):
    move_motors(-vel, -vel)

def move_fw_left(vel):
    # Arc turn left (corrected for hardware swap)
    move_motors(vel * 0.3, vel)

def move_fw_right(vel):
    # Arc turn right (corrected for hardware swap)
    move_motors(vel, vel * 0.3)

def rotate_left(vel):
    # Spin in place to the left 
    move_motors(-vel, vel)

def rotate_right(vel):
    # Spin in place to the right 
    move_motors(vel, -vel)

def stop_motors():
    move_motors(0.0, 0.0)

# =====================================================================
# TEST SEQUENCE
# =====================================================================
if __name__ == "__main__":
    print("Initializing Motor Test Sequence...")
    # Speed is set to 60%. Adjust between 0.0 and 1.0
    test_speed = 0.6 
    
    try:
        print("1. Moving Forward...")
        move_forward(test_speed)
        time.sleep(2)
        
        print("2. Moving Backward...")
        move_back(test_speed)
        time.sleep(2)
        
        print("3. Turning Forward-Left (Arc)...")
        move_fw_left(test_speed)
        time.sleep(2)
        
        print("4. Turning Forward-Right (Arc)...")
        move_fw_right(test_speed)
        time.sleep(2)
        
        print("5. Rotating Left in place...")
        rotate_left(test_speed)
        time.sleep(2)
        
        print("6. Rotating Right in place...")
        rotate_right(test_speed)
        time.sleep(2)
        
        print("7. Testing 'move_motors' with time parameter (Forward for 1.5s)...")
        move_motors(test_speed, test_speed, 1.5)
        
        print("Test Sequence Complete!")
        
    except KeyboardInterrupt:
        print("\nTest interrupted by user.")
        
    finally:
        print("Shutting down motors.")
        stop_motors()