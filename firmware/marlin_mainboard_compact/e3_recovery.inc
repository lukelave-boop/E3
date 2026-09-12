// The native kill latch never returns to Marlin's planner or command queue.
// USART1 is polled with IRQs masked; only the restricted parser below can reset.
#include "HAL/STM32/e3_recovery_protocol.h"

static void e3_recovery_feed_watchdog() { IWDG->KR = 0xAAAA; }

static bool e3_recovery_wait(uint32_t mask) {
  // An iteration bound works even though SysTick is stopped or DWT is stuck.
  // Normal 115200-baud character transmission takes about 87 microseconds.
  for (uint32_t remaining = 100000; remaining; --remaining) {
    if ((USART1->SR & mask) == mask) return true;
    e3_recovery_feed_watchdog();
  }
  return false;
}

static bool e3_recovery_send(const char *text) {
  while (*text) {
    if (!e3_recovery_wait(USART_SR_TXE)) return false;
    USART1->DR = (uint8_t)*text++;
  }
  return e3_recovery_wait(USART_SR_TC);
}

static void e3_recovery_output(GPIO_TypeDef *port, unsigned pin, bool high) {
  port->BSRR = high ? (1UL << pin) : (1UL << (pin + 16));
  port->OTYPER &= ~(1UL << pin);
  port->PUPDR &= ~(3UL << (pin * 2));
  port->MODER = (port->MODER & ~(3UL << (pin * 2))) | (1UL << (pin * 2));
}

__attribute__((noreturn, noinline)) static void e3_recovery_loop() {
  __disable_irq();
  RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_GPIOBEN | RCC_AHB1ENR_GPIOCEN;
  (void)RCC->AHB1ENR;
  __DSB();
  // Reassert electrical outputs directly, independent of disabled timer IRQs.
  e3_recovery_output(GPIOC, 3, true);  // Shared active-low motor enable.
  e3_recovery_output(GPIOA, 0, false);
  e3_recovery_output(GPIOC, 0, false);
  e3_recovery_output(GPIOA, 1, false);
  e3_recovery_output(GPIOA, 7, false);
  e3_recovery_output(GPIOC, 2, false);
  e3_recovery_output(GPIOB, 8, false);
  e3_recovery_output(GPIOB, 6, false);
  e3_recovery_output(GPIOB, 4, false);
  GPIOC->MODER &= ~(3UL << 26); // Stop probe control pulses; no deploy/stow command.
  GPIOC->PUPDR &= ~(3UL << 26);

  // Discard native software queues entirely. An old buffered G-code line is
  // never consumed here. Clear the hardware status/data pair before listening.
  RCC->APB2ENR |= RCC_APB2ENR_USART1EN;
  (void)RCC->APB2ENR;
  USART1->CR1 = 0;
  USART1->CR2 = 0;
  USART1->CR3 = 0; // Disable inherited DMA as well as flow control.
  const uint32_t prescale = (RCC->CFGR >> 13) & 7;
  const uint32_t pclk = SystemCoreClock >> (prescale < 4 ? 0 : prescale - 3);
  USART1->BRR = (pclk + 57600) / 115200;
  GPIOA->AFR[1] = (GPIOA->AFR[1] & ~((15UL << 4) | (15UL << 8))) | (7UL << 4) | (7UL << 8);
  GPIOA->OTYPER &= ~((1UL << 9) | (1UL << 10));
  GPIOA->PUPDR = (GPIOA->PUPDR & ~((3UL << 18) | (3UL << 20))) | (1UL << 20);
  GPIOA->MODER = (GPIOA->MODER & ~((3UL << 18) | (3UL << 20))) | (2UL << 18) | (2UL << 20);
  (void)USART1->SR;
  (void)USART1->DR;
  USART1->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE;
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  E3RecoveryParser parser(DWT->CYCCNT, e3_recovery_send);
  // Delimit any native diagnostic that was cut short when IRQs were stopped.
  e3_recovery_send("\nError:E3RECOVERY:1 HALTED\n");
  for (;;) {
    e3_recovery_feed_watchdog();
    const uint32_t status = USART1->SR;
    int byte = -1;
    if (status & (USART_SR_PE | USART_SR_FE | USART_SR_NE | USART_SR_ORE)) {
      (void)USART1->DR;
      byte = -2;
    } else if (status & USART_SR_RXNE) byte = USART1->DR & 0xFF;
    if (parser.receive(byte) == E3RecoveryParser::RESET) {
      // send() has completed TC successfully. System reset discards all motion
      // state and takes the retained loader's ordinary automatic application
      // boot path. It does not send HOLD or enter update maintenance.
      __DSB();
      NVIC_SystemReset();
    }
  }
}
