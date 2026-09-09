#ifndef E3_AUX_PROTOCOL_H
#define E3_AUX_PROTOCOL_H

#define E3_UPDATER_ID "E3AUX1 UPDATER 0.2.0 BOARD=0401E013"
#define E3_APPLICATION_ID "E3AUX1 APP 0.2.0 BOARD=0401E013 MODE=BENCH OUTPUTS=DISABLED"
#define E3_LINE_CAPACITY 256u

void protocol_init(void);
void protocol_receive(int byte);
void protocol_tick(void);

void application_init(void);
void application_receive(int byte);
void application_tick(void);

#endif
