interface counter_if;
  logic       clk;
  logic       rst_n;
  logic       wr_en;
  logic [3:0] data_i;
  wire  [3:0] data_o;
  wire  [3:0] count;

  clocking cb @(negedge clk);
    output rst_n, wr_en, data_i;
    input  data_o, count;
  endclocking

  modport dut (
    input  clk, rst_n, wr_en, data_i,
    output data_o, count
  );

  modport tb (
    clocking cb
  );
endinterface
