// Byte-stream interface for one side of the TCP co-simulation testbench.
// Each side drives its TX stream and observes an RX stream that the TB top
// cross-wires from the other side's TX.
interface tcp_if (
    input logic clk
);
   // Driven by this side's driver
   logic       tx_valid;
   logic [7:0] tx_data;
   logic       tx_last;

   // Cross-wired in the TB top from the far side's TX
   logic       rx_valid;
   logic [7:0] rx_data;
   logic       rx_last;

   clocking cb @(posedge clk);
      output tx_valid, tx_data, tx_last;
      input rx_valid, rx_data, rx_last;
   endclocking

   modport tb(clocking cb);
endinterface
