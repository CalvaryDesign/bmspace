import paho.mqtt.client as mqtt
import socket
import time
import yaml
import os
import json
import serial
import io
import json
import atexit
import sys
import constants

def config_loader():
    config = {}

    if os.path.exists('/data/options.json'):
        print("Loading options.json")
        with open(r'/data/options.json') as file:
            config = json.load(file)
            print("Config: " + json.dumps(config))
    elif os.path.exists('config.yaml'):
        print("Loading config.yaml")
        with open(r'config.yaml') as file:
            config = yaml.load(file, Loader=yaml.FullLoader)['options']
    else:
        sys.exit("No config file found")

    return config

def mqtt_setup():
    client = mqtt.Client()
    client.username_pw_set(username=config['mqtt_user'], password=config['mqtt_password'])
    client.connect(config['mqtt_host'], config['mqtt_port'], 60)
    client.loop_start()
    time.sleep(2)
    return client

mqtt_connected = False

def on_connect(client, userdata, flags, rc):
    print("MQTT connected with result code "+str(rc))
    client.will_set(config['mqtt_base_topic'] + "/availability", "offline", qos=0, retain=False)
    global mqtt_connected
    mqtt_connected = True

def on_disconnect(client, userdata, rc):
    print("MQTT disconnected with result code "+str(rc))
    global mqtt_connected
    mqtt_connected = False

def exit_handler(client):
    print("Script exiting")
    client.publish(config['mqtt_base_topic'] + "/availability", "offline")
    return

def main():
    global config
    global mqtt_connected

    print("Starting up...")
    config = config_loader()
    client = mqtt_setup()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    atexit.register(exit_handler, client)

    connection_type = config['connection_type']
    bms_serial = config['bms_serial']
    code_running = True
    scan_interval = config['scan_interval']
    debug_output = config['debug_output']
    print_initial = True

    def bms_connect(address, port):

        if connection_type == "Serial":

            try:
                print("trying to connect %s" % bms_serial)
                s = serial.Serial(bms_serial,timeout = 1)
                print("BMS serial connected")
                return s, True
            except IOError as msg:
                print("BMS serial error connecting: %s" % msg)
                return False, False    

        else:

            try:
                print("trying to connect " + address + ":" + str(port))
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect((address, port))
                print("BMS socket connected")
                return s, True
            except OSError as msg:
                print("BMS socket error connecting: %s" % msg)
                return False, False

    def bms_sendData(comms,request=''):

        if connection_type == "Serial":

            try:
                if len(request) > 0:
                    comms.write(request)
                    time.sleep(0.25)
                    return True
            except IOError as e:
                print("BMS serial error: %s" % e)
                # global bms_connected
                return False

        else:

            try:
                if len(request) > 0:
                    comms.send(request)
                    time.sleep(0.25)
                    return True
            except Exception as e:
                print("BMS socket error: %s" % e)
                # global bms_connected
                return False
            
    def bms_get_data(comms):
        try:
            if connection_type == "Serial":
                inc_data = comms.readline()
            else:
                temp = comms.recv(4096)
                temp2 = temp.split(b'\r')
                # Decide which one to take:
                for element in range(0,len(temp2)):
                    SOI = hex(ord(temp2[element][0:1]))
                    if SOI == '0x7e':
                        inc_data = temp2[element] + b'\r'
                        break

                if (len(temp2) > 2) & (debug_output > 0):
                    print("Multiple EOIs detected")
                    print("...for incoming data: " + str(temp) + " |Hex: " + str(temp.hex(' ')))
            return inc_data
        except Exception as e:
            print("BMS socket receive error: %s" % e)
            # global bms_connected
            return False
        
    def chksum_calc(data):

        nonlocal debug_output
        chksum = 0

        try:

            for element in range(1, len(data)): #-5):
                chksum += (data[element])
            
            chksum = chksum % 65536
            chksum = '{0:016b}'.format(chksum)
        
            flip_bits = '' 
            for i in chksum:
                if i == '0':
                    flip_bits += '1'
                else:
                    flip_bits += '0'

            chksum = flip_bits
            chksum = int(chksum,2)+1

            chksum = format(chksum, 'X')

        except Exception as e:
            if debug_output > 0:
                print("Error calculating CHKSUM using data: " + data)
                print("Error details: ", str(e))
            return(False)

        return(chksum)

    def cid2_rtn(rtn):

        # RTN Reponse codes, looking for errors
        if rtn == b'00':
            return False, False
        elif rtn == b'01':
            return True, "RTN Error 01: Undefined RTN error"
        elif rtn == b'02':
            return True, "RTN Error 02: CHKSUM error"
        elif rtn == b'03':
            return True, "RTN Error 03: LCHKSUM error"
        elif rtn == b'04':
            return True, "RTN Error 04: CID2 undefined"
        elif rtn == b'05':
            return True, "RTN Error 05: Undefined error"
        elif rtn == b'06':
            return True, "RTN Error 06: Undefined error"
        elif rtn == b'09':
            return True, "RTN Error 09: Operation or write error"
        else:
            return False, False

    def bms_parse_data(inc_data):

        nonlocal debug_output

        try:
            
            SOI = hex(ord(inc_data[0:1]))
            if SOI != '0x7e':
                return(False,"Incorrect starting byte for incoming data")

            if debug_output > 1:
                print("SOI: ", SOI)
                print("VER: ", inc_data[1:3])
                print("ADR: ", inc_data[3:5])
                print("CID1 (Type): ", inc_data[5:7])

            RTN = inc_data[7:9]
            error, info = cid2_rtn(RTN)
            if error:
                print(error)
                raise Exception(error)
            
            LCHKSUM = inc_data[9]

            if debug_output > 1:
                print("RTN: ", RTN)
                print("LENGTH: ", inc_data[9:13])
                print(" - LCHKSUM: ", LCHKSUM)
                print(" - LENID: ", inc_data[10:13])

            LENID = int(inc_data[10:13],16) #amount of bytes, i.e. 2x hex

            calc_LCHKSUM = lchksum_calc(inc_data[10:13])
            if calc_LCHKSUM == False:
                return(False,"Error calculating LCHKSUM for incoming data")

            if LCHKSUM != ord(calc_LCHKSUM):
                if debug_output > 0:
                    print("LCHKSUM received: " + str(LCHKSUM) + " does not match calculated: " + str(ord(calc_LCHKSUM)))
                return(False,"LCHKSUM received: " + str(LCHKSUM) + " does not match calculated: " + str(ord(calc_LCHKSUM)))

            if debug_output > 1:
                print(" - LENID (int): ", LENID)

            INFO = inc_data[13:13+LENID]

            if debug_output > 1:
                print("INFO: ", INFO)

            CHKSUM = inc_data[13+LENID:13+LENID+4]

            if debug_output > 1:
                print("CHKSUM: ", CHKSUM)

            calc_CHKSUM = chksum_calc(inc_data[:len(inc_data)-5])

            if debug_output > 1:
                print("Calc CHKSUM: ", calc_CHKSUM)
        except Exception as e:
            if debug_output > 0:
                print("Error1 calculating CHKSUM using data: ", inc_data)
            return(False,"Error1 calculating CHKSUM: " + str(e))

        if calc_CHKSUM == False:
            if debug_output > 0:
                print("Error2 calculating CHKSUM using data: ", inc_data)
            return(False,"Error2 calculating CHKSUM")

        if CHKSUM.decode("ASCII") == calc_CHKSUM:
            return(True,INFO)
        else:
            if debug_output > 0:
                print("Received and calculated CHKSUM does not match: Received: " + CHKSUM.decode("ASCII") + ", Calculated: " + calc_CHKSUM)
                print("...for incoming data: " + str(inc_data) + " |Hex: " + str(inc_data.hex(' ')))
                print("Length of incoming data as measured: " + str(len(inc_data)))
                print("SOI: ", SOI)
                print("VER: ", inc_data[1:3])
                print("ADR: ", inc_data[3:5])
                print("CID1 (Type): ", inc_data[5:7])
                print("RTN (decode!): ", RTN)
                print("LENGTH: ", inc_data[9:13])
                print(" - LCHKSUM: ", inc_data[9])
                print(" - LENID: ", inc_data[10:13])
                print(" - LENID (int): ", int(inc_data[10:13],16))
                print("INFO: ", INFO)
                print("CHKSUM: ", CHKSUM)
            return(False,"Checksum error")
        
    def lchksum_calc(lenid):

        chksum = 0

        try:
            
            for element in range(0, len(lenid)):
                chksum += int(chr(lenid[element]),16)

            chksum = chksum % 16
            chksum = '{0:04b}'.format(chksum)

            flip_bits = '' 
            for i in chksum:
                if i == '0':
                    flip_bits += '1'
                else:
                    flip_bits += '0'

            chksum = flip_bits
            chksum = int(chksum,2)

            chksum += 1

            if chksum > 15:
                chksum = 0

            chksum = format(chksum, 'X')

        except:

            print("Error calculating LCHKSUM using LENID: ", lenid)
            return(False)

        return(chksum)
    
    def bms_request(bms, ver=b"\x32\x35",adr=b"\x30\x31",cid1=b"\x34\x36",cid2=b"\x43\x31",info=b"",LENID=False):

        global bms_connected
        nonlocal debug_output
        
        request = b'\x7e'
        request += ver
        request += adr
        request += cid1
        request += cid2

        if not(LENID):
            LENID = len(info)
            LENID = bytes(format(LENID, '03X'), "ASCII")

        if LENID == b'000':
            LCHKSUM = '0'
        else:
            LCHKSUM = lchksum_calc(LENID)
            if LCHKSUM == False:
                return(False,"Error calculating LCHKSUM)")

        request += bytes(LCHKSUM, "ASCII")
        request += LENID
        request += info
        CHKSUM = bytes(chksum_calc(request), "ASCII")
        if CHKSUM == False:
            return(False,"Error calculating CHKSUM)")
        request += CHKSUM
        request += b'\x0d'

        if debug_output > 2:
            print("-> Outgoing Data: ", request)

        if not bms_sendData(bms,request):
            bms_connected = False
            print("Error, connection to BMS lost")
            return(False,"Error, connection to BMS lost")

        inc_data = bms_get_data(bms)

        if inc_data == False:
            print("Error retrieving data from BMS")
            return(False,"Error retrieving data from BMS")

        if debug_output > 2:
            print("<- Incoming data: ", inc_data)

        success, INFO = bms_parse_data(inc_data)

        return(success, INFO)    
    
    def bms_getPackNumber(bms):

        success, INFO = bms_request(bms,cid2=constants.cid2PackNumber)

        if success == False:
            return(False,INFO)    

        try:
            packNumber = int(INFO,16)
        except:
            print("Error extracting total battery count in pack")
            return(False,"Error extracting total battery count in pack")

        return(success,packNumber)

    def bms_getVersion(comms):

        global bms_version

        success, INFO = bms_request(bms,cid2=constants.cid2SoftwareVersion)

        if success == False:
            return(False,INFO)

        try:

            bms_version = bytes.fromhex(INFO.decode("ascii")).decode("ASCII")
            client.publish(config['mqtt_base_topic'] + "/bms_version",bms_version)
            print("BMS Version: " + bms_version)
        except:
            return(False,"Error extracting BMS version")

        return(success,bms_version)

    def bms_getSerial(comms):

        global bms_sn
        global pack_sn

        success, INFO = bms_request(bms,cid2=constants.cid2SerialNumber)

        if success == False:
            print("Error: " + INFO)
            return(False,INFO, False)

        try:

            bms_sn = bytes.fromhex(INFO[0:30].decode("ascii")).decode("ASCII").replace(" ", "")
            pack_sn = bytes.fromhex(INFO[40:68].decode("ascii")).decode("ASCII").replace(" ", "")
            client.publish(config['mqtt_base_topic'] + "/bms_sn",bms_sn)
            client.publish(config['mqtt_base_topic'] + "/pack_sn",pack_sn)
            print("BMS Serial Number: " + bms_sn)
            print("Pack Serial Number: " + pack_sn)

        except:
            return(False,"Error extracting BMS version", False)

        return(success,bms_sn,pack_sn)
    
    def bms_getAnalogData(bms,batNumber):

        nonlocal print_initial
        global cells
        global temps
        global packs
        byte_index = 2
        i_pack = []
        v_pack = []
        i_remain_cap = []
        i_design_cap = []
        cycles = []
        i_full_cap = []
        soc = []
        soh = []

        battery = bytes(format(batNumber, '02X'), 'ASCII')
        # print("Get analog info for battery: ", battery)

        success, inc_data = bms_request(bms,cid2=constants.cid2PackAnalogData,info=battery)

        if success == False:
            return(False,inc_data)

        try:

            packs = int(inc_data[byte_index:byte_index+2],16)
            if print_initial:
                print("Packs: " + str(packs))
            byte_index += 2

            v_cell = {}
            t_cell = {}

            for p in range(1,packs+1):

                if p > 1:
                    cells_prev = cells

                cells = int(inc_data[byte_index:byte_index+2],16)

                #Possible remove this next test as were now testing for the INFOFLAG at the end
                if p > 1:
                    if cells != cells_prev:
                        byte_index += 2
                        cells = int(inc_data[byte_index:byte_index+2],16)
                        if cells != cells_prev:
                            print("Error parsing BMS analog data: Cannot read multiple packs")
                            return(False,"Error parsing BMS analog data: Cannot read multiple packs")

                if print_initial:
                    print("Pack " + str(p) + ", Total cells: " + str(cells))
                byte_index += 2
                
                cell_min_volt = 0
                cell_max_volt = 0

                for i in range(0,cells):
                    v_cell[(p-1,i)] = int(inc_data[byte_index:byte_index+4],16)
                    byte_index += 4
                    client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/v_cells/cell_" + str(i+1) ,str(v_cell[(p-1,i)]))
                    if print_initial:
                        print("Pack " + str(p) +", V Cell" + str(i+1) + ": " + str(v_cell[(p-1,i)]) + " mV")

                    #Calculate cell max and min volt
                    if i == 0:
                        cell_min_volt = v_cell[(p-1,i)]
                        cell_max_volt = v_cell[(p-1,i)]
                    else:
                        if v_cell[(p-1,i)] < cell_min_volt:
                            cell_min_volt = v_cell[(p-1,i)]
                        if v_cell[(p-1,i)] > cell_max_volt:
                            cell_max_volt = v_cell[(p-1,i)]
            
                #Calculate cells max diff volt
                cell_max_diff_volt = cell_max_volt - cell_min_volt
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/cells_max_diff_calc" ,str(cell_max_diff_volt))
                if print_initial:
                    print("Pack " + str(p) +", Cell Max Diff Volt Calc: " + str(cell_max_diff_volt) + " mV")

                temps = int(inc_data[byte_index:byte_index + 2],16)
                if print_initial:
                    print("Pack " + str(p) + ", Total temperature sensors: " + str(temps))
                byte_index += 2

                for i in range(0,temps): #temps-2
                    t_cell[(p-1,i)] = (int(inc_data[byte_index:byte_index + 4],16)-2730)/10
                    byte_index += 4
                    client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/temps/temp_" + str(i+1) ,str(round(t_cell[(p-1,i)],1)))
                    if print_initial:
                        print("Pack " + str(p) + ", Temp" + str(i+1) + ": " + str(round(t_cell[(p-1,i)],1)) + " ℃")

                i_pack.append(int(inc_data[byte_index:byte_index+4],16))
                byte_index += 4
                if i_pack[p-1] >= 32768:
                    i_pack[p-1] = -1*(65535 - i_pack[p-1])
                i_pack[p-1] = i_pack[p-1]/100
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/i_pack",str(i_pack[p-1]))
                if print_initial:
                    print("Pack " + str(p) + ", I Pack: " + str(i_pack[p-1]) + " A")

                v_pack.append(int(inc_data[byte_index:byte_index+4],16)/1000)
                byte_index += 4
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/v_pack",str(v_pack[p-1]))
                if print_initial:
                    print("Pack " + str(p) + ", V Pack: " + str(v_pack[p-1]) + " V")

                i_remain_cap.append(int(inc_data[byte_index:byte_index+4],16)*10)
                byte_index += 4
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/i_remain_cap",str(i_remain_cap[p-1]))
                if print_initial:
                    print("Pack " + str(p) + ", I Remaining Capacity: " + str(i_remain_cap[p-1]) + " mAh")

                byte_index += 2 # Manual: Define number P = 3

                i_full_cap.append(int(inc_data[byte_index:byte_index+4],16)*10)
                byte_index += 4
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/i_full_cap",str(i_full_cap[p-1]))
                if print_initial:
                    print("Pack " + str(p) + ", I Full Capacity: " + str(i_full_cap[p-1]) + " mAh")

                soc.append(round(i_remain_cap[p-1]/i_full_cap[p-1]*100,2))
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/soc",str(soc[p-1]))
                if print_initial:
                    print("Pack " + str(p) + ", SOC: " + str(soc[p-1]) + " %")

                cycles.append(int(inc_data[byte_index:byte_index+4],16))
                byte_index += 4
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/cycles",str(cycles[p-1]))
                if print_initial:
                    print("Pack " + str(p) + ", Cycles: " + str(cycles[p-1]))

                i_design_cap.append(int(inc_data[byte_index:byte_index+4],16)*10)
                byte_index += 4
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/i_design_cap",str(i_design_cap[p-1]))
                if print_initial:
                    print("Pack " + str(p) + ", Design Capacity: " + str(i_design_cap[p-1]) + " mAh")

                soh.append(round(i_full_cap[p-1]/i_design_cap[p-1]*100,2))
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/soh",str(soh[p-1]))
                if print_initial:
                    print("Pack " + str(p) + ", SOH: " + str(soh[p-1]) + " %")

                byte_index += 2

                #Test for non signed value (matching cell count), to skip possible INFOFLAG present in data
                if (byte_index < len(inc_data)) and (cells != int(inc_data[byte_index:byte_index+2],16)):
                    byte_index += 2

        except Exception as e:
            print("Error parsing BMS analog data: ", str(e))
            return(False,"Error parsing BMS analog data: " + str(e))

        if print_initial:
            print("Script running....")

        return True,True
    
    def bms_getPackCapacity(bms):

        byte_index = 0

        success, inc_data = bms_request(bms,cid2=constants.cid2PackCapacity) # Seem to always reply with pack 1 data, even with ADR= 0 or FF and INFO= '' or FF

        if success == False:
            return(False,inc_data)

        try:

            pack_remain_cap = int(inc_data[byte_index:byte_index+4],16)*10
            byte_index += 4
            client.publish(config['mqtt_base_topic'] + "/pack_remain_cap",str(pack_remain_cap))
            if print_initial:
                print("Pack Remaining Capacity: " + str(pack_remain_cap) + " mAh")

            pack_full_cap = int(inc_data[byte_index:byte_index+4],16)*10
            byte_index += 4
            client.publish(config['mqtt_base_topic'] + "/pack_full_cap",str(pack_full_cap))
            if print_initial:
                print("Pack Full Capacity: " + str(pack_full_cap) + " mAh")

            pack_design_cap = int(inc_data[byte_index:byte_index+4],16)*10
            byte_index += 4
            client.publish(config['mqtt_base_topic'] + "/pack_design_cap",str(pack_design_cap))
            if print_initial:
                print("Pack Design Capacity: " + str(pack_design_cap) + " mAh")

            pack_soc = round(pack_remain_cap/pack_full_cap*100,2)
            client.publish(config['mqtt_base_topic'] + "/pack_soc",str(pack_soc))
            if print_initial:
                print("Pack SOC: " + str(pack_soc) + " %")

            pack_soh = round(pack_full_cap/pack_design_cap*100,2)
            client.publish(config['mqtt_base_topic'] + "/pack_soh",str(pack_soh))
            if print_initial:
                print("Pack SOH: " + str(pack_soh) + " %")

        except Exception as e:
            print("Error parsing BMS pack capacity data: ", str(e))
            return False, "Error parsing BMS pack capacity data: " + str(e)

        return True,True

    def bms_getWarnInfo(bms):

        byte_index = 2
        packsW = 1
        warnings = ""

        success, inc_data = bms_request(bms,cid2=constants.cid2WarnInfo,info=b'FF')

        if success == False:
            return(False,inc_data)

        try:

            packsW = int(inc_data[byte_index:byte_index+2],16)
            if print_initial:
                print("Packs for warnings: " + str(packs))
            byte_index += 2

            for p in range(1,packs+1):

                cellsW = int(inc_data[byte_index:byte_index+2],16)
                byte_index += 2

                for c in range(1,cellsW+1):

                    if inc_data[byte_index:byte_index+2] != b'00':
                        warn = constants.warningStates[inc_data[byte_index:byte_index+2]]
                        warnings += "cell " + str(c) + " " + warn + ", "
                    byte_index += 2

                tempsW = int(inc_data[byte_index:byte_index+2],16)
                byte_index += 2
            
                for t in range(1,tempsW+1):

                    if inc_data[byte_index:byte_index+2] != b'00':
                        warn = constants.warningStates[inc_data[byte_index:byte_index+2]]
                        warnings += "temp " + str(t) + " " + warn + ", "
                    byte_index += 2

                if inc_data[byte_index:byte_index+2] != b'00':
                    warn = constants.warningStates[inc_data[byte_index:byte_index+2]]
                    warnings += "charge current " + warn + ", "
                byte_index += 2

                if inc_data[byte_index:byte_index+2] != b'00':
                    warn = constants.warningStates[inc_data[byte_index:byte_index+2]]
                    warnings += "total voltage " + warn + ", "
                byte_index += 2

                if inc_data[byte_index:byte_index+2] != b'00':
                    warn = constants.warningStates[inc_data[byte_index:byte_index+2]]
                    warnings += "discharge current " + warn + ", "
                byte_index += 2

                protectState1 = ord(bytes.fromhex(inc_data[byte_index:byte_index+2].decode('ascii')))
                if protectState1 > 0:
                    warnings += "Protection State 1: "
                    for x in range(0,8):
                        if (protectState1 & (1<<x)):
                            warnings += constants.protectState1[x+1] + " | "
                    warnings = warnings.rstrip("| ")
                    warnings += ", "
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/prot_short_circuit",str(protectState1>>6 & 1))  
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/prot_discharge_current",str(protectState1>>5 & 1))  
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/prot_charge_current",str(protectState1>>4 & 1))  
                byte_index += 2

                protectState2 = ord(bytes.fromhex(inc_data[byte_index:byte_index+2].decode('ascii')))
                if protectState2 > 0:
                    warnings += "Protection State 2: "
                    for x in range(0,8):
                        if (protectState2 & (1<<x)):
                            warnings += constants.protectState2[x+1] + " | "
                    warnings = warnings.rstrip("| ")
                    warnings += ", "
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/fully",str(protectState2>>7 & 1))  
                byte_index += 2

                instructionState = ord(bytes.fromhex(inc_data[byte_index:byte_index+2].decode('ascii')))
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/current_limit",str(instructionState>>0 & 1))
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/charge_fet",str(instructionState>>1 & 1))
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/discharge_fet",str(instructionState>>4 & 1))
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/pack_indicate",str(instructionState>>3 & 1))
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/reverse",str(instructionState>>4 & 1))
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/ac_in",str(instructionState>>5 & 1))
                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/heart",str(instructionState>>7 & 1))
                byte_index += 2

                controlState = ord(bytes.fromhex(inc_data[byte_index:byte_index+2].decode('ascii')))
                if controlState > 0:
                    warnings += "Control State: "
                    for x in range(0,8):
                        if (controlState & (1<<x)):
                            warnings += constants.controlState[x+1] + " | "
                    warnings = warnings.rstrip("| ")
                    warnings += ", "  
                byte_index += 2

                faultState = ord(bytes.fromhex(inc_data[byte_index:byte_index+2].decode('ascii')))
                if faultState > 0:
                    warnings += "Fault State: "
                    for x in range(0,8):
                        if (faultState & (1<<x)):
                            warnings += constants.faultState[x+1] + " | "
                    warnings = warnings.rstrip("| ")
                    warnings += ", "  
                byte_index += 2

                balanceState1 = '{0:08b}'.format(int(inc_data[byte_index:byte_index+2],16))
                byte_index += 2

                balanceState2 = '{0:08b}'.format(int(inc_data[byte_index:byte_index+2],16))
                byte_index += 2

                warnState1 = ord(bytes.fromhex(inc_data[byte_index:byte_index+2].decode('ascii')))
                if warnState1 > 0:
                    warnings += "Warning State 1: "
                    for x in range(0,8):
                        if (warnState1 & (1<<x)):
                            warnings += constants.warnState1[x+1] + " | "
                    warnings = warnings.rstrip("| ")
                    warnings += ", "  
                byte_index += 2

                warnState2 = ord(bytes.fromhex(inc_data[byte_index:byte_index+2].decode('ascii')))
                if warnState2 > 0:
                    warnings += "Warning State 2: "
                    for x in range(0,8):
                        if (warnState2 & (1<<x)):
                            warnings += constants.warnState2[x+1] + " | "
                    warnings = warnings.rstrip("| ")
                    warnings += ", "  
                byte_index += 2

                warnings = warnings.rstrip(", ")

                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/warnings",warnings)
                if print_initial:
                    print("Pack " + str(p) + ", warnings: " + warnings)

                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/balancing1",balanceState1)
                if print_initial:
                    print("Pack " + str(p) + ", balancing1: " + balanceState1)

                client.publish(config['mqtt_base_topic'] + "/pack_" + str(p) + "/balancing2",balanceState2)
                if print_initial:
                    print("Pack " + str(p) + ", balancing2: " + balanceState2)

                warnings = ""

                #Test for non signed value (matching cell count), to skip possible INFOFLAG present in data
                if (byte_index < len(inc_data)) and (cellsW != int(inc_data[byte_index:byte_index+2],16)):
                    byte_index += 2

        except Exception as e:
            print("Error parsing BMS warning data: ", str(e))
            return False, "Error parsing BMS warning data: " + str(e)

        return True,True

    print("Connecting to BMS...")
    bms,bms_connected = bms_connect(config['bms_ip'],config['bms_port'])

    client.publish(config['mqtt_base_topic'] + "/availability","offline")

    success, data = bms_getVersion(bms)
    if success != True:
        print("Error retrieving BMS version number")

    time.sleep(0.1)
    success, bms_sn, pack_sn = bms_getSerial(bms)
    if success != True:
        print("Error retrieving BMS and pack serial numbers. This is required. Exiting...")
        quit()

    repub_discovery = 0
    while code_running == True:

        if bms_connected == True:
            global mqtt_connected
            if mqtt_connected == True:

                success, data = bms_getAnalogData(bms,batNumber=255)
                if success != True:
                    print("Error retrieving BMS analog data: " + data)
                time.sleep(scan_interval/3)
                success, data = bms_getPackCapacity(bms)
                if success != True:
                    print("Error retrieving BMS pack capacity: " + data)
                time.sleep(scan_interval/3)
                success, data = bms_getWarnInfo(bms)
                if success != True:
                    print("Error retrieving BMS warning info: " + data)
                time.sleep(scan_interval/3)

                client.publish(config['mqtt_base_topic'] + "/availability","online")

                print_initial = False
                
                repub_discovery += 1
                if repub_discovery*scan_interval > 3600:
                    repub_discovery = 0
                    print_initial = True
            
            else: #MQTT not connected
                client.loop_stop()
                print("MQTT disconnected, trying to reconnect...")
                client.connect(config['mqtt_host'], config['mqtt_port'], 60)
                client.loop_start()
                time.sleep(5)
                print_initial = True
        else: #BMS not connected
            print("BMS disconnected, trying to reconnect...")
            bms,bms_connected = bms_connect(config['bms_ip'],config['bms_port'])
            client.publish(config['mqtt_base_topic'] + "/availability","offline")
            time.sleep(5)
            print_initial = True

    client.loop_stop()

if __name__ == "__main__":
    main()